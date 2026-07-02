
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
from types import SimpleNamespace

import pygame as pg

from config import TITLE, TILE_LENGTH, ITEM_ICON_SIZE
from world import World, world_to_tile
from render import Screen, Minimap, WorldRenderer
from breaking import BreakSystem
from entity import Entity
from prototype import load_prototype
from inventory import Inventory
from exchange import ExchangePanel
from factory import FactoryPanel
from spot_market import SpotMarket, HISTORY_LEN
from item import DroppedItem, get_item_icon, load_item, format_quantity
from save_state import _rle_decode
from ui_theme import get_font
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


def _center_of(e):
    w, h = e.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
    return (e.world_x + w / 2, e.world_y + h / 2)


def _openable_near(world, wx, wy, local_id):
    # the exchange or machine entity under the cursor, if the local player is
    # within ~6 tiles. None otherwise.
    tx, ty = world_to_tile((wx, wy))
    ent = world.get_entity_at_tile(tx, ty)
    if ent is None or not ('exchange' in ent.components or 'machine' in ent.components):
        return None
    p = world.entities.get(local_id)
    if p is None:
        return None
    ecx, ecy = _center_of(ent)
    pcx, pcy = _center_of(p)
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


def _draw_health_bar(surface, player) -> None:
    if player is None or player.health is None:
        return
    w, h = 260, 20
    x = (surface.get_width() - w) // 2
    y = surface.get_height() - h - 14
    frac = max(0.0, player.health / player.max_health)
    pg.draw.rect(surface, (0, 0, 0), (x - 2, y - 2, w + 4, h + 4))
    pg.draw.rect(surface, (150, 40, 40), (x, y, w, h))
    if frac > 0:
        pg.draw.rect(surface, (70, 200, 80), (x, y, int(w * frac), h))
    pg.draw.rect(surface, (235, 235, 235), (x, y, w, h), width=1)


def _attack_target_at(world, wx: float, wy: float, local_id):
    # the mob whose body is under the cursor (None if the click missed).
    for ent in world.entities.values():
        if ent.id == local_id:
            continue
        if 'mob' in ent.components and ent.hitbox_rect().collidepoint(wx, wy):
            return ent
    return None


def _draw_overhead_bars(surface, world, cam, local_id) -> None:
    # small green/red bar over any wounded non-local entity (mobs + other
    # players). the local player uses the bottom-of-screen bar instead.
    for ent in world.entities.values():
        if ent.id == local_id or ent.health is None or ent.max_health is None:
            continue
        if ent.health >= ent.max_health:
            continue
        hb = ent.hitbox_rect()
        bx, by = cam.world_to_screen((hb.centerx - 22, hb.top - 12))
        bx, by = int(bx), int(by)
        frac = max(0.0, ent.health / ent.max_health)
        pg.draw.rect(surface, (20, 20, 24), (bx - 1, by - 1, 46, 7))
        pg.draw.rect(surface, (150, 40, 40), (bx, by, 44, 5))
        if frac > 0:
            pg.draw.rect(surface, (70, 200, 80), (bx, by, int(44 * frac), 5))


def _draw_death(surface) -> None:
    w, h = surface.get_size()
    veil = pg.Surface((w, h), pg.SRCALPHA)
    veil.fill((0, 0, 0, 180))
    surface.blit(veil, (0, 0))
    label = get_font(72).render('YOU DIED', True, (170, 30, 30))
    surface.blit(label, label.get_rect(center=(w // 2, h // 2)))


def _draw_held(surface, held) -> None:
    # the drag cursor: the held stack's icon following the mouse.
    if not held:
        return
    icon = get_item_icon(load_item(held['item_id']), size=ITEM_ICON_SIZE)
    mx, my = pg.mouse.get_pos()
    surface.blit(icon, (mx - icon.get_width() // 2, my - icon.get_height() // 2))
    if held['quantity'] > 1:
        font = get_font(16)
        text = format_quantity(held['quantity'])
        r = font.render(text, True, (255, 255, 255)).get_rect(
            bottomright=(mx + icon.get_width() // 2, my + icon.get_height() // 2))
        surface.blit(font.render(text, True, (0, 0, 0)), r.move(1, 1))
        surface.blit(font.render(text, True, (255, 255, 255)), r)


# --- main ---

def run(host: str = '127.0.0.1', port: int = 5555) -> None:
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
    screen = Screen(1280, 720)
    world = World()
    _build_world(world, welcome['world'], local_id)
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
    exchange_panel = ExchangePanel(net_spot, inventory, SimpleNamespace(day=0))
    factory_panel = FactoryPanel()

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
    running = True
    while running and net_alive['ok']:
        dt = clock.tick(120) / 1000.0
        for event in pg.event.get():
            if event.type == pg.QUIT:
                running = False
            elif event.type == pg.KEYDOWN:
                if event.key == pg.K_ESCAPE:
                    if exchange_panel.open:
                        exchange_panel.close()
                    elif factory_panel.open:
                        factory_panel.close()
                        try:
                            netproto.send(sock, {'type': 'close_machine'})
                        except OSError:
                            running = False
                    else:
                        running = False
                elif event.key == pg.K_b:
                    inventory.toggle()
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
                    if exchange_panel.open and exchange_panel.hit(pos):
                        exchange_panel.handle_click(pos, None)   # spot only
                        if exchange_panel.tabs is not None:
                            exchange_panel.tabs.active = 0   # lock to Spot; forward/dropbox aren't networked
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
                        if held is not None:
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
                                    tx, ty = world_to_tile((wx, wy))
                                    if world.overlay_at(tx, ty) is not None:
                                        netproto.send(sock, {'type': 'break', 'tile': [tx, ty]})
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
                    latest_snap = msg
                elif mtype == 'inv':
                    lp = world.entities.get(local_id)
                    if lp is not None:
                        lp.inventory.slots = msg['slots']
                        lp.held_item = msg.get('held')
                elif mtype == 'machine':
                    ent = world.entities.get(msg['id'])
                    if ent is not None and 'machine' in ent.components:
                        ms = ent.components['machine']
                        ms['input_slots'] = msg['input']
                        ms['output_slots'] = msg['output']
                        ms['current_recipe'] = msg['recipe']
                        ms['started_ms'] = pg.time.get_ticks() - msg['elapsed']
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

        if player is not None:
            sw, sh = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
            screen.camera.follow((player.world_x, player.world_y), sprite_size=(sw, sh))

        screen.clear()
        world_renderer.flush(screen.camera, screen.culling, None)
        # over-head bars are world-space, so draw them onto the offscreen
        # world surface and present (scale by zoom) before the native-res ui.
        _draw_overhead_bars(screen.world_surface, world, screen.camera, local_id)
        screen.present_world()
        if player is not None:
            sw, sh = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
            minimap.render(
                screen.surface, (screen.width, screen.height), screen.camera.offset,
                (player.world_x + sw / 2, player.world_y + sh / 2),
            )
            inventory.render(screen.surface)
        exchange_panel.render(screen.surface, (screen.width, screen.height))
        factory_panel.render(screen.surface, (screen.width, screen.height))
        if player is not None:
            _draw_held(screen.surface, player.held_item)
        _draw_health_bar(screen.surface, player)
        if dead:
            _draw_death(screen.surface)
        pg.display.flip()

    print('[client] disconnected')
    try:
        sock.close()
    except OSError:
        pass
    pg.quit()


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5555
    run(host, port)


if __name__ == '__main__':
    main()
