
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

import sys

import pygame as pg

from config import TITLE, TILE_LENGTH, ITEM_ICON_SIZE, TABS_DIR
from world import World, world_to_tile, in_reach
from render import Screen, Minimap, WorldRenderer, MapView, get_overview
from breaking import BreakSystem, BreakState
from combat import CombatSystem
from entity import Entity
from prototype import load_prototype
from inventory import Inventory
from exchange import ExchangePanel
from factory import FactoryPanel
from spot_market import SpotMarket, HISTORY_LEN
from clock import DayClock
from item import DroppedItem
from settings import load_settings
from display import DisplayService
from settings_panel import SettingsPanel
from player_panel import PlayerPanel
from hud import Hud
from hud_tabs import HudTabs
from persp_debug import PerspectivePanel
from dev_console import DevConsole
import hud_render
import interaction
import movement
import netproto
import keybinds
import skills
import effects
from transport import SocketTransport, LocalTransport

# net smoothing feel (data/balance.json): how fast the local player eases to
# server truth, how fast remote entities ease to their target, and the desync
# past which we snap instead of easing (respawn / teleport).
from balance import PREDICT_CORRECT, INTERP_RATE, SNAP_DIST


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
            ent.max_health = e['mhp']   # tracks a Health-level max-hp bump mid-session
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


def _build_world(world, wd, local_id, map_grid, overlay_grid) -> None:
    world.load_grids(wd['width'], wd['height'], map_grid, overlay_grid)
    # rock-patch descriptors ride in the welcome; the client bakes the visuals
    # locally from their seeds (load_grids cleared the list, so set it after).
    world.rock_patches = netproto.decode_patches(wd.get('rock_patches', []))
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
        mx, my = movement.input_delta(p, dx, dy, dt)
        movement.move_axis(world, p, mx, my)
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

def _poll_move_dir(kb):
    keys = pg.key.get_pressed()
    dx = (1 if keybinds.pressed(keys, kb['move_right']) else 0) \
        - (1 if keybinds.pressed(keys, kb['move_left']) else 0)
    dy = (1 if keybinds.pressed(keys, kb['move_down']) else 0) \
        - (1 if keybinds.pressed(keys, kb['move_up']) else 0)
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

def _begin_break(world, local_id, break_system, transport, tile) -> None:
    p = world.entities.get(local_id)
    if p is None or not in_reach(p, *tile):
        return   # in-reach only for now (no walk-to-break over the net yet)
    found = interaction.breakable_at(world, tile)
    if found is None:
        return
    proto, entity_id = found
    if not interaction.can_mine(p, proto):
        req = getattr(proto, 'mining_level', 1) or 1
        break_system.gate_msg = (f'Requires Mining level {req}', pg.time.get_ticks())
        return
    break_time = proto.break_time or 0.0
    if break_time <= 0:
        transport.send({'type': 'break', 'tile': [tile[0], tile[1]]})   # instant: no timer
        return
    break_system.breaking = BreakState(
        start_ms=pg.time.get_ticks(),
        duration_ms=int(break_time * 1000),
        tile=tile,
        entity_id=entity_id,
    )


def _update_break(world, local_id, break_system, transport) -> None:
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
        transport.send({'type': 'break', 'tile': [bk.tile[0], bk.tile[1]]})
        break_system.breaking = None


# --- the client ---

class Client:
    # thin game client driven by a Transport. it builds its world from the join
    # payload, applies inbound messages, turns input into command intents, and
    # predicts/interpolates + renders — it never runs the authoritative sim.
    #
    # the loop is transport-agnostic: SocketTransport drives it over the network
    # today, and the in-process LocalTransport (single-player listen server) will
    # drive this exact class next, so there's one client code path for both modes.

    def __init__(self, transport, join) -> None:
        self.transport = transport
        self.local_id = join['player_id']
        wd = join['world']
        print(f'[client] connected as {self.local_id}')

        pg.init()
        pg.display.set_caption(f'{TITLE} (client {self.local_id})')
        self.kb = keybinds.load_keybinds()   # rebindable controls (settings.json)
        self.settings = load_settings()
        self.screen = Screen(self.settings['screen_width'], self.settings['screen_height'],
                             display_mode=self.settings['display_mode'])
        self.world = World()
        _build_world(self.world, wd, self.local_id, join['map_grid'], join['overlay_grid'])
        # build the render overview now (during the blocking join) so the first
        # frame doesn't stall on it; the minimap/map then just slice this array.
        get_overview(self.world)
        # local read-only day clock: elapsed is overwritten from the server each
        # snapshot (never ticked here, so no client-side rollover side effects).
        self.day_clock = DayClock()
        self.day_clock.elapsed = wd.get('day_elapsed', 0.0)
        self.minimap = Minimap(self.world)
        self.world_renderer = WorldRenderer(self.screen, self.world, BreakSystem(self.world))
        # floating damage numbers only: the client doesn't run the combat sim, but
        # reuses CombatSystem's number pipeline, fed by 'hit' events off the wire.
        self.combat = CombatSystem(self.world)
        # read-only inventory panel over the local player's synced slots (B toggles).
        self.inventory = Inventory(get_data=lambda: self.world.entities[self.local_id].inventory)
        self._reanchor_inventory()

        self.net_spot = _NetSpotMarket(self._send_trade)
        # seed prices from the join snapshot so a freshly-joined client shows real
        # prices immediately, instead of blanks until the first per-tick snapshot.
        self.net_spot.apply_prices(wd.get('spot_prices', {}))

        self.exchange_panel = ExchangePanel(
            self.net_spot, self.inventory, self.day_clock,
            get_exchange_state=lambda: self.world.entities[self.local_id].exchange_state,
            on_accept=lambda idx: self.transport.send({'type': 'accept', 'index': idx}),
            on_cancel=lambda idx: self.transport.send({'type': 'cancel', 'index': idx}),
            on_dropbox_click=self._send_dropbox,
        )
        self.factory_panel = FactoryPanel()

        # client shell: display facade + settings modal (ESC) + full-screen map
        # (Tab) + diagnostics/day HUD (F3) + right-edge tabs. reuses the same
        # decoupled widgets as single-player.
        self.display = DisplayService(self.settings, self.screen, on_resize=self._reanchor_inventory)
        self.hud = Hud()
        self.hud.visible = self.settings.get('show_hud', True)
        self.map_view = MapView(self.world)
        self.ui_state = {'quit': False, 'title': False}   # settings Quit / Back to Title
        self.settings_panel = SettingsPanel(
            self.display, on_save=None,
            on_quit=lambda: self.ui_state.__setitem__('quit', True),
            show_save=False,
            on_title=lambda: self.ui_state.__setitem__('title', True),
        )
        self.player_panel = PlayerPanel()
        self.hud_tabs = HudTabs(self.screen, [
            ('player', f'{TABS_DIR}/player.png', self._toggle_player),
            ('inventory', f'{TABS_DIR}/backpack.png', self._toggle_inventory),
            ('settings', f'{TABS_DIR}/settings.png', self._toggle_settings),
        ])
        self.level_toasts = hud_render.LevelUpToasts()
        # temporary on-screen controls for the perspective-ground prototype.
        self.persp_panel = PerspectivePanel(self.screen.perspective)
        # developer console (backtick): single-player only. its command table
        # comes from the transport — LocalTransport exposes admin_commands()
        # (direct sim mutation), SocketTransport doesn't, so multiplayer has none.
        self.dev_console = None
        if hasattr(transport, 'admin_commands'):
            self.dev_console = DevConsole(transport.admin_commands())

        self.clock = pg.time.Clock()
        self.last_dir = None
        self.prev_hp = None       # local player's hp last frame; detect drops for the
        self.hp_rattle_ms = None  # health-bar rattle (server owns knockback + dust)
        self.build_mode = False
        # yellow X at the last world-click point, faded out by the renderer. purely
        # local (the click target is this client's aim), so it's set here rather
        # than networked — matches single-player.
        self.click_marker = None
        # entity id of a building we've clicked but must walk to before opening;
        # the host walks us there (plain walk intent) and _update_pending_open
        # opens its panel on arrival. cleared on manual movement / a new click.
        self.pending_open = None
        self.running = True

    # --- intent senders + ui toggles (passed as widget callbacks) ---

    def _send_trade(self, side, item, qty) -> None:
        self.transport.send({'type': 'trade', 'side': side, 'item': item, 'qty': qty})

    def _send_dropbox(self, idx, held):
        # server owns the drop box + held cursor; it syncs the result back on the
        # next inv/exchange message, so we don't mutate locally.
        self.transport.send({'type': 'dropbox', 'slot': idx})
        return held

    def _reanchor_inventory(self) -> None:
        self.inventory.origin = (16, self.screen.height - self.inventory.panel_image.get_height() - 16)
        self.inventory.rect.topleft = self.inventory.origin

    # the backpack, settings, and character sheet are mutually exclusive — opening
    # any one closes the other two.
    def _open_settings(self) -> None:
        self.inventory.open = False
        self.player_panel.close()
        self.settings_panel.open_panel((self.screen.width, self.screen.height))

    def _toggle_settings(self) -> None:
        if self.settings_panel.open:
            self.settings_panel.close()
        else:
            self._open_settings()

    def _toggle_player(self) -> None:
        if not self.player_panel.open:
            self.inventory.open = False
            self.settings_panel.close()
        self.player_panel.toggle()

    def _toggle_inventory(self) -> None:
        if not self.inventory.open:
            self.player_panel.close()
            self.settings_panel.close()
        self.inventory.toggle()

    # --- input ---

    def _handle_events(self) -> None:
        dc = self.dev_console
        for event in pg.event.get():
            if event.type == pg.QUIT:
                self.running = False
            elif event.type == pg.KEYDOWN:
                # the console (single-player) captures all keys while open;
                # backtick toggles it, otherwise gameplay keys run.
                if dc is not None and dc.open:
                    dc.handle_event(event)
                elif dc is not None and event.key == self.kb['dev_console']:
                    dc.toggle()
                else:
                    self._on_keydown(event)
            elif event.type == pg.TEXTINPUT:
                # the real character stream goes to the console field while open.
                if dc is not None and dc.open:
                    dc.handle_event(event)
            elif event.type == pg.MOUSEWHEEL:
                self._on_wheel(event)
            elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                self._on_click(event.pos)
            elif event.type == pg.MOUSEBUTTONUP and event.button == 1:
                self.persp_panel.handle_release()   # end a slider drag
            elif event.type == pg.MOUSEMOTION:
                self.persp_panel.handle_motion(event.pos)   # live slider drag

    def _on_keydown(self, event) -> None:
        kb = self.kb
        if event.key == kb['menu']:
            # close the top open overlay; if none, open the settings modal.
            if self.map_view.open:
                self.map_view.close()
            elif self.player_panel.open:
                self.player_panel.close()
            elif self.settings_panel.open:
                self.settings_panel.close()
            elif self.exchange_panel.open:
                self.exchange_panel.close()
            elif self.factory_panel.open:
                self.factory_panel.close()
                self.transport.send({'type': 'close_machine'})
            else:
                self._open_settings()
        elif event.key == kb['inventory']:
            self._toggle_inventory()
        elif event.key == kb['build']:
            self.build_mode = not self.build_mode
        elif event.key == kb['map']:
            self.map_view.toggle()
        elif event.key == kb['display_mode']:
            self.display.cycle_mode()
        elif event.key == kb['hud']:
            self.hud.toggle()
        # tier-1 perspective ground prototype: F6 toggles, F7/F8 tune strength.
        elif event.key == pg.K_F6:
            p = self.screen.perspective
            p.enabled = not p.enabled
        elif event.key == pg.K_F7:
            p = self.screen.perspective
            p.strength = max(0.0, round(p.strength - 0.02, 2))
        elif event.key == pg.K_F8:
            p = self.screen.perspective
            p.strength = min(0.8, round(p.strength + 0.02, 2))

    def _on_wheel(self, event) -> None:
        if self.exchange_panel.open:
            self.exchange_panel.handle_scroll(pg.mouse.get_pos(), event.y)
        elif self.player_panel.open:
            self.player_panel.handle_scroll(pg.mouse.get_pos(), event.y)
        else:
            self.screen.zoom_by(event.y)

    def _on_click(self, pos) -> None:
        # cascade top-to-bottom; the first consumer returns. mirrors single-
        # player's _on_left_click, but world actions become intents.
        if self.dev_console is not None and self.dev_console.open:
            return   # console open: swallow world/ui clicks behind it
        if self.persp_panel.handle_click(pos):
            return   # perspective debug panel consumed the click
        world, local_id = self.world, self.local_id
        lp = world.entities.get(local_id)
        held = lp.held_item if lp is not None else None
        if self.hud_tabs.handle_click(pos):
            return   # a right-edge tab consumed the click
        if self.map_view.open:
            return   # swallow world clicks while the full-screen map is up
        if self.settings_panel.open:
            if self.settings_panel.hit(pos):
                self.settings_panel.handle_click(pos)
            else:
                self.settings_panel.close()
            return
        if self.player_panel.hit(pos):
            return   # character sheet is display-only: swallow the click
        if self.exchange_panel.open and self.exchange_panel.hit(pos):
            self.exchange_panel.handle_click(pos, held)   # spot/forward/drop-box route through intents
            return
        if self.factory_panel.open and self.factory_panel.hit(pos):
            kind, idx = self.factory_panel.slot_at_pixel(pos)
            self.transport.send({'type': 'machine_click', 'kind': kind, 'slot': idx})
            return
        if self.inventory.open and self.inventory.rect.collidepoint(pos):
            slot = self.inventory.slot_at_pixel(pos)
            if slot is not None:
                self.transport.send({'type': 'inv_click', 'slot': slot})
            return
        if self.exchange_panel.open:
            self.exchange_panel.close()
            return
        if self.factory_panel.open:
            self.factory_panel.close()
            self.transport.send({'type': 'close_machine'})
            return
        self._on_world_click(pos, lp, held)

    def _adjacent_to(self, player, ent) -> bool:
        # is `player` within one tile of any of the entity's footprint tiles?
        # (the reach a building needs to be opened.)
        return any(in_reach(player, ftx, fty, max_dist=1) for ftx, fty in ent.footprint())

    def _open_entity(self, ent) -> None:
        # machines need an open_machine intent so the host syncs their slots to
        # this viewer; the exchange renders from already-synced state.
        if 'machine' in ent.components:
            self.factory_panel.open_for(ent, (self.screen.width, self.screen.height))
            self.transport.send({'type': 'open_machine', 'id': ent.id})
        else:
            self.exchange_panel.open_for(ent, (self.screen.width, self.screen.height))

    def _update_pending_open(self, move_dir) -> None:
        # open a queued building once the host has walked us adjacent to it.
        # cancels on manual movement or if it despawned.
        if self.pending_open is None:
            return
        if move_dir[0] or move_dir[1]:
            self.pending_open = None
            return
        ent = self.world.entities.get(self.pending_open)
        if ent is None:
            self.pending_open = None
            return
        lp = self.world.entities.get(self.local_id)
        if lp is not None and self._adjacent_to(lp, ent):
            self._open_entity(ent)
            self.pending_open = None

    def _on_world_click(self, pos, lp, held) -> None:
        world, local_id = self.world, self.local_id
        wx, wy = self.screen.camera.pick(pos)   # perspective-aware mouse -> world
        self.pending_open = None   # a fresh world click cancels a queued open
        if self.build_mode:
            tile = world_to_tile((wx, wy))
            if _client_can_place(world, local_id, held, tile):
                self.transport.send({'type': 'place', 'tile': list(tile)})
            return
        if held is not None:
            self.transport.send({'type': 'drop', 'x': wx, 'y': wy})
            return
        tile = world_to_tile((wx, wy))
        # openable (factory/exchange): open now if adjacent, else walk there and
        # open on arrival (host walks; _update_pending_open opens).
        ent = interaction.openable_at(world, tile)
        if ent is not None:
            self.click_marker = ((wx, wy), pg.time.get_ticks())
            if lp is not None and self._adjacent_to(lp, ent):
                self._open_entity(ent)
            else:
                self.pending_open = ent.id
                self.transport.send({'type': 'walk', 'x': wx, 'y': wy})
            return
        target = _attack_target_at(world, wx, wy, local_id)
        # drop the aim X wherever an actionable world click lands (mirrors sp).
        self.click_marker = ((wx, wy), pg.time.get_ticks())
        if target is not None:
            # host walks to the mob (if out of range) then swings.
            self.transport.send({'type': 'attack', 'target': target.id})
            return
        found = interaction.breakable_at(world, tile)
        if found is not None:
            if lp is not None and in_reach(lp, *tile):
                # in reach: run the local break timer (progress bar); it fires the
                # break intent on completion.
                _begin_break(world, local_id, self.world_renderer.break_system, self.transport, tile)
            elif lp is not None and not interaction.can_mine(lp, found[0]):
                # gate locally for immediate feedback so we don't trek to a tile
                # we can't mine yet.
                req = getattr(found[0], 'mining_level', 1) or 1
                self.world_renderer.break_system.gate_msg = (
                    f'Requires Mining level {req}', pg.time.get_ticks())
            else:
                # out of reach: let the host walk there then break.
                self.transport.send({'type': 'break', 'tile': list(tile)})
            return
        # empty ground: walk there.
        self.transport.send({'type': 'walk', 'x': wx, 'y': wy})

    # --- inbound state ---

    def _apply_inbound(self) -> None:
        # drain inbound messages: apply overlay deltas + events from EVERY
        # snapshot (they're incremental), but only the latest snapshot's
        # entity/dropped state (those are absolute). a tick sends a snapshot THEN
        # an inv, so we dispatch by type, not last-msg-wins.
        world, local_id = self.world, self.local_id
        latest_snap = None
        for msg in self.transport.poll():
            mtype = msg.get('type')
            if mtype == 'snapshot':
                _apply_overlay(world, self.minimap, msg.get('overlay', []))
                # one-shot fx (attack swings, hit dust + numbers). applied from
                # EVERY snapshot so a swing isn't dropped when two land in a frame.
                now_ms = pg.time.get_ticks()
                for ev in msg.get('events', []):
                    effects.apply(world, self.world_renderer.break_system, self.combat, ev, now_ms)
                self.net_spot.apply_prices(msg.get('prices', {}))
                self.day_clock.elapsed = msg.get('day_elapsed', self.day_clock.elapsed)
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
            elif mtype == 'skills':
                lp = world.entities.get(local_id)
                if lp is not None and lp.skills is not None:
                    # apply the authoritative xp, and derive level-up toasts
                    # from the diff (no separate event channel needed).
                    for track, xp in msg['skills'].items():
                        if track not in lp.skills:
                            continue
                        before = skills.level_of(lp.skills, track)
                        lp.skills[track] = xp
                        after = skills.level_of(lp.skills, track)
                        for lvl in range(before + 1, after + 1):
                            world.pending_level_ups.append((track, lvl))
            elif mtype == 'machine':
                ent = world.entities.get(msg['id'])
                if ent is not None and 'machine' in ent.components:
                    ms = ent.components['machine']
                    ms['input_slots'] = msg['input']
                    ms['output_slots'] = msg['output']
                    ms['current_recipe'] = msg['recipe']
                    ms['elapsed_ms'] = msg['elapsed']
        if latest_snap is not None:
            _apply_entities(world, latest_snap['ents'], local_id)
            _apply_dropped(world, latest_snap['dropped'])

    # --- local advance (prediction + interpolation) ---

    def _step(self, dt: float):
        # returns (player, dead) for the render pass. sends the movement intent
        # on change, predicts the local player, interpolates everyone else.
        world, local_id = self.world, self.local_id
        player = world.entities.get(local_id)
        dead = player is not None and player.health is not None and player.health <= 0

        # frozen while dead or while typing in the dev console.
        typing = self.dev_console is not None and self.dev_console.open
        move_dir = (0, 0) if (dead or typing) else _poll_move_dir(self.kb)
        if move_dir != self.last_dir:
            self.transport.send({'type': 'move', 'dx': move_dir[0], 'dy': move_dir[1]})
            self.last_dir = move_dir
        # open a queued building once we've been walked adjacent to it.
        self._update_pending_open(move_dir)

        _step_local(world, local_id, move_dir, dt)
        _step_remote(world, local_id, dt)
        # advance the local break timer (progress bar); fires the intent on done.
        _update_break(world, local_id, self.world_renderer.break_system, self.transport)
        # advance transient particles (hit dust); the server does the authoritative
        # tile clear, so the sp finalize path is skipped.
        self.world_renderer.break_system.tick_particles(dt)
        self.combat.tick(pg.time.get_ticks())   # age floating damage numbers
        # advance the open machine's craft bar locally between server updates so
        # it stays smooth; each 'machine' message re-syncs it to authoritative.
        if self.factory_panel.open and self.factory_panel.entity is not None:
            fms = self.factory_panel.entity.components.get('machine')
            if fms and fms.get('current_recipe') is not None:
                fms['elapsed_ms'] = fms.get('elapsed_ms', 0.0) + dt * 1000.0

        if player is not None:
            self.screen.camera.follow((player.world_x, player.world_y), sprite_size=player.sprite_dims)
        return player, dead

    # --- render ---

    def _render(self, dt: float, player, dead) -> None:
        screen, world = self.screen, self.world
        screen.clear()
        self.click_marker = self.world_renderer.flush(screen.camera, screen.culling, self.click_marker)
        # over-head bars + floating damage numbers are world-space, so draw them
        # onto the offscreen world surface and present (scale by zoom) before ui.
        _draw_overhead_bars(screen.world_surface, world, screen.camera, self.local_id)
        self.combat.render_numbers(screen.world_surface, screen.camera, pg.time.get_ticks())
        if self.build_mode and player is not None:
            hud_render.draw_build_highlight(screen.world_surface, world, screen.camera,
                                            player, player.held_item, pg.mouse.get_pos())
        screen.present_world()
        # screen-space ui
        self.hud.render(screen.surface, fps=self.clock.get_fps(), frame_ms=dt * 1000,
                        n_entities=len(world.entities), n_dropped=len(world.dropped))
        self.hud.render_day_counter(screen.surface, day=self.day_clock.day)
        if player is not None:
            self.minimap.render(screen.surface, (screen.width, screen.height),
                                screen.camera.offset, player.center)
            self.inventory.render(screen.surface)
        self.hud_tabs.render(screen.surface)
        self.persp_panel.render(screen.surface)
        self.exchange_panel.render(screen.surface, (screen.width, screen.height))
        self.factory_panel.render(screen.surface, (screen.width, screen.height))
        self.settings_panel.render(screen.surface, (screen.width, screen.height))
        self.player_panel.render(screen.surface, (screen.width, screen.height), player)
        self.map_view.render(screen.surface, (screen.width, screen.height), screen.camera, player)
        # skill feedback: level-up toasts + a gated-break message.
        toast_now = pg.time.get_ticks()
        self.level_toasts.pump(world, toast_now)
        self.level_toasts.render(screen.surface, toast_now)
        hud_render.draw_gate_message(screen.surface, self.world_renderer.break_system, toast_now)
        if player is not None:
            hud_render.draw_held_cursor(screen.surface, player.held_item, pg.mouse.get_pos(),
                                        anchor='center', icon_size=ITEM_ICON_SIZE, shadow=True)
        # detect an hp drop this frame (server integrates knockback + damage) to
        # rattle the health bar. the hit dust + number come from the 'hit' event.
        if player is not None and player.health is not None:
            if self.prev_hp is not None and player.health < self.prev_hp:
                self.hp_rattle_ms = pg.time.get_ticks()
            self.prev_hp = player.health
        shake = hud_render.health_bar_shake(self.hp_rattle_ms, pg.time.get_ticks())
        hud_render.draw_health_bar(screen.surface, player, shake=shake)
        if self.build_mode:
            hud_render.draw_build_indicator(screen.surface)
        if dead:
            hud_render.draw_death_overlay(screen.surface, opaque=False)
        if self.dev_console is not None:
            self.dev_console.render(screen.surface)

    # --- loop ---

    def step_frame(self) -> bool:
        # advance exactly one frame (input -> intents, apply inbound, predict/
        # interpolate, render) and return whether the client should keep running.
        # exposed apart from run() so a headless test can pump frames.
        dt = self.clock.tick(120) / 1000.0
        self._handle_events()
        self._apply_inbound()
        player, dead = self._step(dt)
        self._render(dt, player, dead)
        pg.display.flip()
        return (self.running and self.transport.alive()
                and not self.ui_state['quit'] and not self.ui_state['title'])

    def run(self) -> str | None:
        while self.step_frame():
            pass
        print('[client] disconnected')
        self.transport.close()
        # pygame stays initialized so the launcher can reuse the window (main.py
        # owns the final pg.quit()).
        return 'title' if self.ui_state['title'] else None


# --- entry points ---

def run(host: str = '127.0.0.1', port: int = 5555) -> str | None:
    # multiplayer: connect the socket transport (blocks through the join
    # handshake), then hand it to the Client. kept as a module function so the
    # launcher's call is unchanged.
    transport = SocketTransport(host, port)
    try:
        join = transport.connect()
    except ConnectionError as exc:
        print(f'[client] {exc}')
        return None
    return Client(transport, join).run()


def run_local(save_path: str | None = None, world_name: str | None = None) -> str | None:
    # single-player: a listen server. an in-process LocalTransport owns the sim +
    # host; the Client drives it through the exact same loop multiplayer uses.
    # returns 'title' (Back to Title) or None (quit), like run().
    transport = LocalTransport(save_path, world_name)
    return Client(transport, transport.connect()).run()


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5555
    run(host, port)


if __name__ == '__main__':
    main()
