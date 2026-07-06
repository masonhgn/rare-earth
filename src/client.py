
# networked thin client.
#
# connects to the authoritative server, builds its world from the join
# snapshot, applies per-tick snapshots, and renders — it does NOT run the
# authoritative sim. run the server first (python src/server.py), then:
#   python src/client.py [host] [port]
#
# smoothing: each snapshot sets every entity's server position as an
# interpolation TARGET (net_x/net_y); the rendered position (world_x/y) eases
# toward it each frame. the local player is additionally PREDICTED from input
# (instant response) and gently corrected to the server's authoritative
# position, so it doesn't feel laggy at the 20Hz tick.
#
# v1 scope: shared-world rendering + movement + health bar. break/attack/
# inventory/economy intents + the panels come with Phase 3.

import queue
import socket
import sys
import threading

import pygame as pg

from config import TITLE, TILE_LENGTH, ITEM_ICON_SIZE
from world import World, world_to_tile, in_reach
from render import Screen, Minimap, WorldRenderer, MapView
from breaking import BreakSystem, BreakState
from entity import Entity
from prototype import load_prototype
from inventory import Inventory
from exchange import ExchangePanel
from factory import FactoryPanel
from spot_market import SpotMarket, HISTORY_LEN
from clock import DayClock
from item import DroppedItem
from save_state import _rle_decode
from settings import load_settings
from display import DisplayService
from settings_panel import SettingsPanel
from hud import Hud
from hud_tabs import HudTabs
import hud_render
import interaction
import movement
import netproto

DIAG = 0.7071067811865475   # diagonal movement normalization (matches server)
PREDICT_CORRECT = 10.0      # per-second rate the local player eases to server truth
INTERP_RATE = 12.0          # per-second rate remote entities ease to their target
SNAP_DIST = 96.0            # local desync past this snaps instead of easing (respawn/teleport)


class _NetSpotMarket(SpotMarket):
    # client spot market: prices come from the server (apply_prices); trades
    # send an intent instead of mutating locally — the server validates +
    # applies and the result rides back on the inventory sync.
    def __init__(self, send_trade):
        super().__init__()
        self._send_trade = send_trade

    def sell(self, inventory, item_id, qty=1):
        if qty > 0:
            self._send_trade('sell', item_id, qty)
        return True

    def buy(self, inventory, item_id, qty=1):
        if qty > 0:
            self._send_trade('buy', item_id, qty)
        return True

    def apply_prices(self, prices) -> None:
        # overwrite mids; append to a sparkline history only when a price
        # actually moved (the server steps it every ~5s).
        for item_id, price in prices.items():
            if self.prices.get(item_id) != price:
                hist = self.history.setdefault(item_id, [])
                hist.append(price)
                if len(hist) > HISTORY_LEN:
                    del hist[:-HISTORY_LEN]
            self.prices[item_id] = price


def _openable_near(world, wx, wy, local_id):
    # the interactable entity under or adjacent to the cursor, if the local
    # player is within ~6 tiles. None otherwise.
    ent = interaction.openable_at(world, world_to_tile((wx, wy)))
    if ent is None:
        return None
    p = world.entities.get(local_id)
    if p is None:
        return None
    ecx, ecy = ent.center
    pcx, pcy = p.center
    if (ecx - pcx) ** 2 + (ecy - pcy) ** 2 <= (6 * TILE_LENGTH) ** 2:
        return ent
    return None


# --- applying server state ---

def _apply_entities(world, ents, local_id) -> None:
    # store the server position as each entity's interpolation target (net_x/y);
    # world_x/y (rendered) is eased toward it per frame, or predicted for the
    # local player.
    seen = set()
    for e in ents:
        seen.add(e['id'])
        ent = world.entities.get(e['id'])
        if ent is None:
            ent = Entity(load_prototype(e['proto']), (e['x'], e['y']), entity_id=e['id'])
            ent.max_health = e['mhp']
            ent.health = e['hp']
            ent.net_x, ent.net_y = e['x'], e['y']
            try:
                world.add_entity(ent)
            except ValueError:
                pass   # tile already occupied — shouldn't happen from the server
        else:
            ent.net_x, ent.net_y = e['x'], e['y']
            ent.health = e['hp']
            if ent.id == local_id and (abs(ent.net_x - ent.world_x) > SNAP_DIST
                                       or abs(ent.net_y - ent.world_y) > SNAP_DIST):
                ent.world_x, ent.world_y = ent.net_x, ent.net_y   # big desync: snap
    for eid in list(world.entities.keys()):
        if eid not in seen:
            world.remove_entity(eid)


def _apply_dropped(world, dropped) -> None:
    world.dropped = [DroppedItem(d['item'], d['q'], d['x'], d['y']) for d in dropped]
    world._rebuild_grid()


def _apply_overlay(world, minimap, changes) -> None:
    # cleared ore tiles (mining). apply every snapshot's deltas (not just the
    # latest) so a skipped frame doesn't leave stale ore on the client.
    for tx, ty in changes:
        if 0 <= ty < world.height and 0 <= tx < world.width:
            world.overlay_grid[ty][tx] = None
            minimap.update_cell(tx, ty)


def _build_world(world, wd, local_id) -> None:
    world.width, world.height = wd['width'], wd['height']
    world.map_grid = _rle_decode(wd['map_grid'], world.width, world.height)
    world.overlay_grid = _rle_decode(wd['overlay_grid'], world.width, world.height)
    world.entities.clear()
    world.tile_index.clear()
    world.dropped.clear()
    world.spatial_grid.clear()
    _apply_entities(world, wd['ents'], local_id)
    _apply_dropped(world, wd['dropped'])


# --- per-frame smoothing ---

def _step_local(world, local_id, move_dir, dt) -> None:
    # predict the local player from held input, then ease toward the server's
    # authoritative position (covers knockback / collision / prediction drift).
    p = world.entities.get(local_id)
    if p is None:
        return
    ox, oy = p.world_x, p.world_y
    dx, dy = move_dir
    if dx or dy:
        if dx and dy:
            dx *= DIAG
            dy *= DIAG
        speed = p.prototype.speed or 0.0
        movement.move_axis(world, p, dx * speed * dt, dy * speed * dt)
    t = min(1.0, PREDICT_CORRECT * dt)
    p.world_x += (p.net_x - p.world_x) * t
    p.world_y += (p.net_y - p.world_y) * t
    movement.update_player_animation(p, p.world_x - ox, p.world_y - oy)


def _step_remote(world, local_id, dt) -> None:
    # ease every non-local entity toward its server target + drive its walk
    # animation from the resulting motion.
    t = min(1.0, INTERP_RATE * dt)
    for ent in world.entities.values():
        if ent.id == local_id:
            continue
        ox, oy = ent.world_x, ent.world_y
        ent.world_x += (ent.net_x - ent.world_x) * t
        ent.world_y += (ent.net_y - ent.world_y) * t
        movement.update_player_animation(ent, ent.world_x - ox, ent.world_y - oy)


# --- input + hud ---

def _poll_move_dir():
    keys = pg.key.get_pressed()
    dx = (1 if keys[pg.K_d] else 0) - (1 if keys[pg.K_a] else 0)
    dy = (1 if keys[pg.K_s] else 0) - (1 if keys[pg.K_w] else 0)
    return (dx, dy)


def _attack_target_at(world, wx: float, wy: float, local_id):
    # the mob whose body is under the cursor (None if the click missed).
    return interaction.mob_at(world, wx, wy, exclude_id=local_id)


def _draw_overhead_bars(surface, world, cam, local_id) -> None:
    # keep the client's gating (health < max — no synced last_damage_ms);
    # the per-bar draw is shared with single-player.
    for ent in world.entities.values():
        if ent.id == local_id or ent.health is None or ent.max_health is None:
            continue
        if ent.health >= ent.max_health:
            continue
        hud_render.draw_overhead_bar(surface, cam, ent)


# --- build-mode placement (client side) ---

def _client_can_place(world, local_id, held, tile) -> bool:
    # client-side placement validity, delegating to the shared check so it
    # always matches the server. keyed on local_id (the client's player isn't
    # the fixed-id 'player').
    p = world.entities.get(local_id)
    return p is not None and interaction.can_place(world, p, tile, held)


# --- break (client side): timed progress bar, server-authoritative finalize ---
#
# the client runs the break TIMER locally (so the progress bar + per-material
# duration feel right) but never mutates the world itself — on completion it
# sends a break intent and the server does the authoritative clear, which comes
# back as an overlay delta / entity despawn. keyed on local_id since the client's
# player isn't the fixed-id 'player' the World reach helper assumes.

def _begin_break(world, local_id, break_system, sock, tile) -> None:
    p = world.entities.get(local_id)
    if p is None or not in_reach(p, *tile):
        return   # in-reach only for now (no walk-to-break over the net yet)
    found = interaction.breakable_at(world, tile)
    if found is None:
        return
    proto, entity_id = found
    break_time = proto.break_time or 0.0
    if break_time <= 0:
        try:   # instant material: no timer, just fire the intent
            netproto.send(sock, {'type': 'break', 'tile': [tile[0], tile[1]]})
        except OSError:
            pass
        return
    break_system.breaking = BreakState(
        start_ms=pg.time.get_ticks(),
        duration_ms=int(break_time * 1000),
        tile=tile,
        entity_id=entity_id,
    )


def _update_break(world, local_id, break_system, sock) -> None:
    bk = break_system.breaking
    if bk is None:
        return
    # cancel if the target vanished (someone else / the server cleared it) or we
    # walked out of reach.
    p = world.entities.get(local_id)
    gone = (bk.entity_id not in world.entities) if bk.entity_id is not None \
        else (world.overlay_at(*bk.tile) is None)
    if p is None or gone or not in_reach(p, *bk.tile):
        break_system.breaking = None
        return
    if bk.is_complete(pg.time.get_ticks()):
        try:
            netproto.send(sock, {'type': 'break', 'tile': [bk.tile[0], bk.tile[1]]})
        except OSError:
            pass
        break_system.breaking = None


# --- main ---

def run(host: str = '127.0.0.1', port: int = 5555) -> str | None:
    try:
        sock = socket.create_connection((host, port))
    except OSError as exc:
        print(f'[client] could not connect to {host}:{port} ({exc}). is the server running?')
        return
    welcome = netproto.recv(sock)
    if welcome is None or welcome.get('type') != 'welcome':
        print('[client] no welcome from server')
        return
    local_id = welcome['player_id']
    print(f'[client] connected as {local_id}')

    pg.init()
    pg.display.set_caption(f'{TITLE} (client {local_id})')
    settings = load_settings()
    screen = Screen(settings['screen_width'], settings['screen_height'],
                    display_mode=settings['display_mode'])
    world = World()
    _build_world(world, welcome['world'], local_id)
    # local read-only day clock: elapsed is overwritten from the server each
    # snapshot (never ticked here, so no client-side rollover side effects).
    day_clock = DayClock()
    day_clock.elapsed = welcome['world'].get('day_elapsed', 0.0)
    minimap = Minimap(world)
    world_renderer = WorldRenderer(screen, world, BreakSystem(world))
    # read-only inventory panel over the local player's synced slots (B toggles).
    inventory = Inventory(get_data=lambda: world.entities[local_id].inventory)
    inventory.origin = (16, screen.height - inventory.panel_image.get_height() - 16)
    inventory.rect.topleft = inventory.origin

    def _send_trade(side, item, qty):
        try:
            netproto.send(sock, {'type': 'trade', 'side': side, 'item': item, 'qty': qty})
        except OSError:
            pass
    net_spot = _NetSpotMarket(_send_trade)
    # seed prices from the join snapshot so a freshly-joined client shows real
    # prices immediately, instead of blanks until the first per-tick snapshot.
    net_spot.apply_prices(welcome['world'].get('spot_prices', {}))

    def _send_intent(msg):
        try:
            netproto.send(sock, msg)
        except OSError:
            pass

    def _send_dropbox(idx, held):
        # server owns the drop box + held cursor; it syncs the result back on
        # the next inv/exchange message, so we don't mutate locally.
        _send_intent({'type': 'dropbox', 'slot': idx})
        return held

    exchange_panel = ExchangePanel(
        net_spot, inventory, day_clock,
        get_exchange_state=lambda: world.entities[local_id].exchange_state,
        on_accept=lambda idx: _send_intent({'type': 'accept', 'index': idx}),
        on_cancel=lambda idx: _send_intent({'type': 'cancel', 'index': idx}),
        on_dropbox_click=_send_dropbox,
    )
    factory_panel = FactoryPanel()

    # client shell: display facade + settings modal (ESC) + full-screen map
    # (Tab) + diagnostics/day HUD (F3) + right-edge tabs. reuses the same
    # decoupled widgets as single-player.
    def _reanchor_inventory():
        inventory.origin = (16, screen.height - inventory.panel_image.get_height() - 16)
        inventory.rect.topleft = inventory.origin
    display = DisplayService(settings, screen, on_resize=_reanchor_inventory)
    hud = Hud()
    hud.visible = settings.get('show_hud', True)
    map_view = MapView(world)
    ui_state = {'quit': False, 'title': False}   # settings Quit / Back to Title
    settings_panel = SettingsPanel(
        display, on_save=None,
        on_quit=lambda: ui_state.__setitem__('quit', True),
        show_save=False,
        on_title=lambda: ui_state.__setitem__('title', True),
    )

    def _toggle_settings():
        if settings_panel.open:
            settings_panel.close()
        else:
            settings_panel.open_panel((screen.width, screen.height))
    hud_tabs = HudTabs(screen, [
        ('inventory', 'src/data/sprites/ui/tabs/backpack.png', inventory.toggle),
        ('settings', 'src/data/sprites/ui/tabs/settings.png', _toggle_settings),
    ])

    incoming: queue.Queue = queue.Queue()
    net_alive = {'ok': True}

    def net_loop():
        # any exit — clean EOF, reset, or a malformed frame — flags the main
        # loop so the client doesn't render stale state forever.
        try:
            while True:
                msg = netproto.recv(sock)
                if msg is None:
                    break
                incoming.put(msg)
        except Exception:
            pass
        net_alive['ok'] = False

    threading.Thread(target=net_loop, daemon=True).start()

    clock = pg.time.Clock()
    last_dir = None
    build_mode = False
    running = True
    while running and net_alive['ok'] and not ui_state['quit'] and not ui_state['title']:
        dt = clock.tick(120) / 1000.0
        for event in pg.event.get():
            if event.type == pg.QUIT:
                running = False
            elif event.type == pg.KEYDOWN:
                if event.key == pg.K_ESCAPE:
                    # close the top open overlay; if none, open the settings modal.
                    if map_view.open:
                        map_view.close()
                    elif settings_panel.open:
                        settings_panel.close()
                    elif exchange_panel.open:
                        exchange_panel.close()
                    elif factory_panel.open:
                        factory_panel.close()
                        try:
                            netproto.send(sock, {'type': 'close_machine'})
                        except OSError:
                            running = False
                    else:
                        settings_panel.open_panel((screen.width, screen.height))
                elif event.key == pg.K_b:
                    inventory.toggle()
                elif event.key == pg.K_g:
                    build_mode = not build_mode
                elif event.key == pg.K_TAB:
                    map_view.toggle()
                elif event.key == pg.K_F2:
                    display.cycle_mode()
                elif event.key == pg.K_F3:
                    hud.toggle()
            elif event.type == pg.MOUSEWHEEL:
                if exchange_panel.open:
                    exchange_panel.handle_scroll(pg.mouse.get_pos(), event.y)
                else:
                    screen.zoom_by(event.y)
            elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                pos = event.pos
                lp = world.entities.get(local_id)
                held = lp.held_item if lp is not None else None
                try:
                    if hud_tabs.handle_click(pos):
                        pass   # a right-edge tab consumed the click
                    elif map_view.open:
                        pass   # swallow world clicks while the full-screen map is up
                    elif settings_panel.open:
                        if settings_panel.hit(pos):
                            settings_panel.handle_click(pos)
                        else:
                            settings_panel.close()
                    elif exchange_panel.open and exchange_panel.hit(pos):
                        # spot / forward / drop-box all route through intents now
                        exchange_panel.handle_click(pos, held)
                    elif factory_panel.open and factory_panel.hit(pos):
                        kind, idx = factory_panel.slot_at_pixel(pos)
                        netproto.send(sock, {'type': 'machine_click', 'kind': kind, 'slot': idx})
                    elif inventory.open and inventory.rect.collidepoint(pos):
                        slot = inventory.slot_at_pixel(pos)
                        if slot is not None:
                            netproto.send(sock, {'type': 'inv_click', 'slot': slot})
                    elif exchange_panel.open:
                        exchange_panel.close()
                    elif factory_panel.open:
                        factory_panel.close()
                        netproto.send(sock, {'type': 'close_machine'})
                    else:
                        wx, wy = screen.camera.screen_to_world(pos)
                        if build_mode:
                            tile = world_to_tile((wx, wy))
                            if _client_can_place(world, local_id, held, tile):
                                netproto.send(sock, {'type': 'place', 'tile': list(tile)})
                        elif held is not None:
                            netproto.send(sock, {'type': 'drop', 'x': wx, 'y': wy})
                        else:
                            ent = _openable_near(world, wx, wy, local_id)
                            if ent is not None and 'machine' in ent.components:
                                factory_panel.open_for(ent, (screen.width, screen.height))
                                netproto.send(sock, {'type': 'open_machine', 'id': ent.id})
                            elif ent is not None:
                                exchange_panel.open_for(ent, (screen.width, screen.height))
                            else:
                                target = _attack_target_at(world, wx, wy, local_id)
                                if target is not None:
                                    netproto.send(sock, {'type': 'attack', 'target': target.id})
                                else:
                                    _begin_break(world, local_id, world_renderer.break_system,
                                                 sock, world_to_tile((wx, wy)))
                except OSError:
                    running = False

        # drain incoming: apply overlay deltas from EVERY snapshot (they're
        # incremental) + the latest inventory, but only the latest snapshot's
        # entity/dropped state (those are absolute). a tick sends a snapshot
        # THEN an inv, so we must dispatch by type, not just keep the last msg.
        latest_snap = None
        try:
            while True:
                msg = incoming.get_nowait()
                mtype = msg.get('type')
                if mtype == 'snapshot':
                    _apply_overlay(world, minimap, msg.get('overlay', []))
                    net_spot.apply_prices(msg.get('prices', {}))
                    day_clock.elapsed = msg.get('day_elapsed', day_clock.elapsed)
                    latest_snap = msg
                elif mtype == 'inv':
                    lp = world.entities.get(local_id)
                    if lp is not None:
                        lp.inventory.slots = msg['slots']
                        lp.held_item = msg.get('held')
                elif mtype == 'exchange':
                    lp = world.entities.get(local_id)
                    if lp is not None:
                        lp.components['player']['exchange'] = msg['state']
                elif mtype == 'machine':
                    ent = world.entities.get(msg['id'])
                    if ent is not None and 'machine' in ent.components:
                        ms = ent.components['machine']
                        ms['input_slots'] = msg['input']
                        ms['output_slots'] = msg['output']
                        ms['current_recipe'] = msg['recipe']
                        ms['elapsed_ms'] = msg['elapsed']
        except queue.Empty:
            pass
        if latest_snap is not None:
            _apply_entities(world, latest_snap['ents'], local_id)
            _apply_dropped(world, latest_snap['dropped'])

        player = world.entities.get(local_id)
        dead = player is not None and player.health is not None and player.health <= 0

        # movement intent (frozen while dead); send only on change
        move_dir = (0, 0) if dead else _poll_move_dir()
        if move_dir != last_dir:
            try:
                netproto.send(sock, {'type': 'move', 'dx': move_dir[0], 'dy': move_dir[1]})
                last_dir = move_dir
            except OSError:
                break

        # smooth: predict the local player, interpolate everyone else
        _step_local(world, local_id, move_dir, dt)
        _step_remote(world, local_id, dt)
        # advance the local break timer (progress bar); fires the intent on done
        _update_break(world, local_id, world_renderer.break_system, sock)
        # advance the open machine's craft progress locally between server
        # updates so the bar is smooth; each 'machine' message re-syncs it.
        if factory_panel.open and factory_panel.entity is not None:
            fms = factory_panel.entity.components.get('machine')
            if fms and fms.get('current_recipe') is not None:
                fms['elapsed_ms'] = fms.get('elapsed_ms', 0.0) + dt * 1000.0

        if player is not None:
            screen.camera.follow((player.world_x, player.world_y), sprite_size=player.sprite_dims)

        screen.clear()
        world_renderer.flush(screen.camera, screen.culling, None)
        # over-head bars are world-space, so draw them onto the offscreen
        # world surface and present (scale by zoom) before the native-res ui.
        _draw_overhead_bars(screen.world_surface, world, screen.camera, local_id)
        if build_mode and player is not None:
            hud_render.draw_build_highlight(screen.world_surface, world, screen.camera,
                                            player, player.held_item, pg.mouse.get_pos())
        screen.present_world()
        # screen-space ui
        hud.render(screen.surface, fps=clock.get_fps(), frame_ms=dt * 1000,
                   n_entities=len(world.entities), n_dropped=len(world.dropped))
        hud.render_day_counter(screen.surface, day=day_clock.day)
        if player is not None:
            minimap.render(
                screen.surface, (screen.width, screen.height), screen.camera.offset,
                player.center,
            )
            inventory.render(screen.surface)
        hud_tabs.render(screen.surface)
        exchange_panel.render(screen.surface, (screen.width, screen.height))
        factory_panel.render(screen.surface, (screen.width, screen.height))
        settings_panel.render(screen.surface, (screen.width, screen.height))
        map_view.render(screen.surface, (screen.width, screen.height), screen.camera)
        if player is not None:
            hud_render.draw_held_cursor(screen.surface, player.held_item, pg.mouse.get_pos(),
                                        anchor='center', icon_size=ITEM_ICON_SIZE, shadow=True)
        hud_render.draw_health_bar(screen.surface, player)
        if build_mode:
            hud_render.draw_build_indicator(screen.surface)
        if dead:
            hud_render.draw_death_overlay(screen.surface, opaque=False)
        pg.display.flip()

    print('[client] disconnected')
    try:
        sock.close()
    except OSError:
        pass
    # pygame stays initialized so the launcher can reuse the window (main.py
    # owns the final pg.quit()).
    return 'title' if ui_state['title'] else None


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5555
    run(host, port)


if __name__ == '__main__':
    main()
