
# authoritative TCP game server.
#
# runs the headless SimCore, accepts client connections (one player entity per
# connection), applies their movement intents, and broadcasts world snapshots
# every tick. start with:   python src/server.py
#
# this is the connect-locally milestone slice: shared world + movement. break /
# attack / economy intents, state deltas, interest management, prediction, and
# persistence all come in later phases. for now clients render the full snapshot.

import asyncio
import math
import os
import traceback

# run headless — cloud boxes have no display/audio. must be set before pygame
# initializes its subsystems (pg.init in GameServer.__init__).
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import pygame as pg

from config import PLAYER_SPAWN, PLAYER_ATTACK_RANGE, TILE_LENGTH
from simcore import SimCore
from entity import Entity
from prototype import load_prototype
from world import tile_center, world_to_tile, in_reach
from item import roll_drops, load_item
from contracts import accept_contract, cancel_contract, ensure_board
import interaction
import movement
import netproto
import slots as slot_ops

TICK_HZ = 20
DIAG = 0.7071067811865475   # diagonal movement normalization (matches client)
PLAYER_ATTACK_CD = 0.4      # seconds between a player's melee swings
DEATH_SCREEN_SEC = 2.0      # dead players freeze (hp 0) this long before respawn
SAVE_INTERVAL = 60.0        # seconds between shared-world autosaves
MAX_WRITE_BUFFER = 1 << 20  # drop a client whose unsent send buffer exceeds this (1 MB)


# --- intent input validation (clients are untrusted; reject malformed values
# before they reach the unguarded sim path) ---

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _finite(v):
    # a finite float, or None if v isn't a real (non-NaN/Inf) number.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _axis(v) -> float:
    # movement axis coerced to [-1, 1] — rejects NaN/Inf and speed/teleport hacks.
    f = _finite(v)
    return 0.0 if f is None else max(-1.0, min(1.0, f))


class Connection:
    def __init__(self, writer, player_id: str) -> None:
        self.writer = writer
        self.player_id = player_id
        self.move_dir = (0.0, 0.0)   # latest held WASD direction from this client
        self.pending_attack = None   # entity id this client wants to hit (one-shot)
        self.pending_break = None    # (tx, ty) tile this client wants to break (one-shot)
        self.pending_place = None    # (tx, ty) tile to place the held item on (one-shot)
        self.pending_trade = None    # (side, item_id, qty) spot trade request (one-shot)
        self.actions = []              # ordered held-cursor actions this tick (inv/machine/drop)
        self.open_machine = None       # entity id of the machine this client has open
        self.attack_cd = 0.0         # seconds until this player may swing again
        self.dead_until = None       # sim-elapsed time to respawn at, or None if alive


class GameServer:
    def __init__(self) -> None:
        # pygame is initialized (no display) only because the sim systems still
        # read pg.time.get_ticks() for break/factory/combat timing. (Phase 4
        # replaces that with the fixed-step sim clock and drops this.)
        pg.init()
        # load the persisted shared world if one exists, else seed a fresh one.
        self.sim = SimCore(seed_default=False)
        if self.sim.load():
            print('[server] loaded saved world')
        else:
            self.sim.seed()
            print('[server] seeded a fresh world')
        # the default single-player 'player' from World() isn't used here —
        # players join per connection with unique ids.
        self.sim.world.remove_entity('player')
        self.conns: dict[int, Connection] = {}
        self._next_id = 0
        self._elapsed = 0.0   # accumulated sim seconds (drives death/respawn timing)
        self._overlay_changes: list = []   # (tx, ty) ore tiles cleared since last broadcast
        self._save_accum = 0.0   # seconds since the last autosave
        self.max_players = int(os.environ.get('MAX_PLAYERS', '16'))

    # --- per-connection lifecycle ---

    async def handle_client(self, reader, writer) -> None:
        # refuse new players past the cap — each connection is a player entity in
        # the world, so an unbounded flood would exhaust the sim.
        if len(self.conns) >= self.max_players:
            writer.close()
            return
        pid = f'player_{self._next_id}'
        self._next_id += 1
        self.sim.world.add_entity(
            Entity(load_prototype('player'), PLAYER_SPAWN, entity_id=pid)
        )
        # give the new player their own contract board (per-player ownership).
        ensure_board(self.sim.world.entities[pid].exchange_state, self.sim.spot_market)
        conn = Connection(writer, pid)
        self.conns[id(writer)] = conn
        peer = writer.get_extra_info('peername')
        print(f'[server] {peer} joined as {pid} ({len(self.conns)} online)')

        writer.write(netproto.encode({
            'type': 'welcome',
            'player_id': pid,
            'world': netproto.world_join(self.sim.world, self.sim.spot_market, self.sim.day_clock),
        }))
        try:
            await writer.drain()
            while True:
                msg = await netproto.read_msg(reader)
                if msg is None:
                    break
                mtype = msg.get('type')
                if mtype == 'move':
                    conn.move_dir = (_axis(msg.get('dx')), _axis(msg.get('dy')))
                elif mtype == 'attack':
                    tid = msg.get('target')
                    if isinstance(tid, str):
                        conn.pending_attack = tid
                elif mtype == 'break':
                    t = msg.get('tile')
                    if isinstance(t, list) and len(t) == 2 and _is_int(t[0]) and _is_int(t[1]):
                        conn.pending_break = (int(t[0]), int(t[1]))
                elif mtype == 'place':
                    t = msg.get('tile')
                    if isinstance(t, list) and len(t) == 2 and _is_int(t[0]) and _is_int(t[1]):
                        conn.pending_place = (int(t[0]), int(t[1]))
                elif mtype == 'trade':
                    side, item, qty = msg.get('side'), msg.get('item'), msg.get('qty')
                    if side in ('sell', 'buy') and isinstance(item, str) and _is_int(qty):
                        conn.pending_trade = (side, item, int(qty))
                elif mtype == 'inv_click':
                    if _is_int(msg.get('slot')):
                        conn.actions.append(('inv', int(msg['slot'])))
                elif mtype == 'drop':
                    x, y = _finite(msg.get('x')), _finite(msg.get('y'))
                    if x is not None and y is not None:
                        conn.actions.append(('drop', x, y))
                elif mtype == 'accept':
                    if _is_int(msg.get('index')):
                        conn.actions.append(('accept', int(msg['index'])))
                elif mtype == 'cancel':
                    if _is_int(msg.get('index')):
                        conn.actions.append(('cancel', int(msg['index'])))
                elif mtype == 'dropbox':
                    if _is_int(msg.get('slot')):
                        conn.actions.append(('dropbox', int(msg['slot'])))
                elif mtype == 'machine_click':
                    kind = msg.get('kind')
                    if kind in ('input', 'output') and _is_int(msg.get('slot')):
                        conn.actions.append(('machine', kind, int(msg['slot'])))
                elif mtype == 'open_machine':
                    mid = msg.get('id')
                    if isinstance(mid, str):
                        conn.open_machine = mid
                elif mtype == 'close_machine':
                    conn.open_machine = None
        finally:
            self.conns.pop(id(writer), None)
            p = self.sim.world.entities.get(pid)
            if p is not None:
                self._drop_player_goods(p, include_dropbox=True)   # spill goods + drop box so a disconnect doesn't sink them
            self.sim.world.remove_entity(pid)
            try:
                writer.close()
            except Exception:
                pass
            print(f'[server] {pid} left ({len(self.conns)} online)')

    # --- authoritative tick ---

    def _apply_move_intents(self, dt: float) -> None:
        for conn in self.conns.values():
            if conn.dead_until is not None:
                continue   # frozen while dead
            p = self.sim.world.entities.get(conn.player_id)
            if p is None:
                continue
            dx, dy = conn.move_dir
            if dx or dy:
                p.path = []
                if dx and dy:
                    dx *= DIAG
                    dy *= DIAG
                speed = p.prototype.speed or 0.0
                movement.move_axis(self.sim.world, p, dx * speed * dt, dy * speed * dt)
            movement.apply_knockback(self.sim.world, p, dt)
            movement.clamp_to_bounds(self.sim.world, p)

    def _process_attacks(self) -> None:
        # apply each client's pending attack: validate target + range + cooldown,
        # then knock back + damage. random damage roll happens server-side.
        for conn in self.conns.values():
            target_id = conn.pending_attack
            conn.pending_attack = None
            if target_id is None or conn.dead_until is not None or conn.attack_cd > 0.0:
                continue
            p = self.sim.world.entities.get(conn.player_id)
            target = self.sim.world.entities.get(target_id)
            if (p is None or target is None or target.health is None
                    or 'mob' not in target.components):
                continue   # PvE only: targets must be mobs
            (pcx, pcy), (tcx, tcy) = p.center, target.center
            if (pcx - tcx) ** 2 + (pcy - tcy) ** 2 <= PLAYER_ATTACK_RANGE ** 2:
                movement.knock_back(p, target)
                self.sim.combat.hit(target, pg.time.get_ticks())
                conn.attack_cd = PLAYER_ATTACK_CD

    def _process_breaks(self) -> None:
        # instant break (no progress bar over the net for v1): validate the
        # acting player's reach, then clear the ore / break the entity and spawn
        # its drops. records cleared overlay tiles for the snapshot delta.
        for conn in self.conns.values():
            tile = conn.pending_break
            conn.pending_break = None
            if tile is None or conn.dead_until is not None:
                continue
            p = self.sim.world.entities.get(conn.player_id)
            if p is None:
                continue
            tx, ty = tile
            if not in_reach(p, tx, ty):
                continue   # out of reach
            self._break_tile(tx, ty)

    def _break_tile(self, tx: int, ty: int) -> None:
        w = self.sim.world
        found = interaction.breakable_at(w, (tx, ty))
        if found is None:
            return
        proto, entity_id = found
        if entity_id is not None:
            for item_id, qty in w.break_entity(entity_id):
                w.spawn_dropped_item(item_id, qty, tile_center((tx, ty)))
            return
        # overlay ore: clear the tile, record the delta for the snapshot, drop.
        w.overlay_grid[ty][tx] = None
        self._overlay_changes.append((tx, ty))
        for item_id, qty in roll_drops(proto.drops):
            w.spawn_dropped_item(item_id, qty, tile_center((tx, ty)))

    def _process_places(self) -> None:
        # build-mode placement: validate reach + emptiness + that the acting
        # player's held item is placeable, then spawn the entity and consume one
        # unit from held. the new entity rides out on the next snapshot; the
        # decremented held rides back on the per-player inv message. the
        # single-threaded tick serializes this, so two players racing for the
        # same tile can't double-place (the loser's add_entity raises).
        for conn in self.conns.values():
            tile = conn.pending_place
            conn.pending_place = None
            if tile is None or conn.dead_until is not None:
                continue
            p = self.sim.world.entities.get(conn.player_id)
            if p is None or p.held_item is None:
                continue
            proto = load_item(p.held_item['item_id'])
            if proto.places is None:
                continue
            tx, ty = tile
            if not self._placeable_here(p, tx, ty):
                continue
            entity = Entity(load_prototype(proto.places), (tx * TILE_LENGTH, ty * TILE_LENGTH))
            try:
                self.sim.world.add_entity(entity)
            except ValueError:
                continue   # lost the tile race this tick — keep the held stack
            held = p.held_item
            if held['quantity'] <= 1:
                p.held_item = None
            else:
                p.held_item = {**held, 'quantity': held['quantity'] - 1}

    def _placeable_here(self, p, tx: int, ty: int) -> bool:
        return interaction.can_place(self.sim.world, p, (tx, ty), p.held_item)

    def _process_player_actions(self) -> None:
        # ordered held-cursor actions (inventory drag, machine in/out, drop) in
        # exact click order, so cross-panel moves work regardless of panel.
        for conn in self.conns.values():
            actions = conn.actions
            conn.actions = []
            if conn.dead_until is not None:
                continue
            p = self.sim.world.entities.get(conn.player_id)
            if p is None:
                continue
            for action in actions:
                if action[0] == 'inv':
                    p.held_item = slot_ops.click(p.inventory.slots, action[1], p.held_item)
                elif action[0] == 'drop':
                    self._drop_held(p, action[1], action[2])
                elif action[0] == 'machine':
                    ent = (self.sim.world.entities.get(conn.open_machine)
                           if conn.open_machine else None)
                    if ent is None or 'machine' not in ent.components:
                        continue
                    ms = ent.components['machine']
                    if action[1] == 'output':
                        p.held_item = slot_ops.click(ms['output_slots'], action[2], p.held_item, take_only=True)
                    else:
                        p.held_item = slot_ops.click(ms['input_slots'], action[2], p.held_item)
                elif action[0] == 'accept':
                    accept_contract(p.exchange_state, action[1], p.inventory, self.sim.day_clock.day)
                elif action[0] == 'cancel':
                    cancel_contract(p.exchange_state, action[1], p.inventory)
                elif action[0] == 'dropbox':
                    p.held_item = slot_ops.click(p.exchange_state['drop_box'], action[1], p.held_item)

    def _drop_held(self, p, x: float, y: float) -> None:
        if p.held_item is None:
            return
        tx, ty = world_to_tile((x, y))
        if not in_reach(p, tx, ty):
            return   # out of reach: keep holding it
        self.sim.world.spawn_dropped_item(p.held_item['item_id'], p.held_item['quantity'], (x, y))
        p.held_item = None

    def _machine_msg(self, machine_id):
        # per-viewer machine state: slots + active recipe + accumulated craft
        # time (elapsed_ms). the client advances it locally between updates so
        # the bar stays smooth, and each message re-syncs it to authoritative.
        ent = self.sim.world.entities.get(machine_id)
        if ent is None or 'machine' not in ent.components:
            return None
        ms = ent.components['machine']
        recipe = ms['current_recipe']
        elapsed = ms['elapsed_ms'] if recipe else 0
        return netproto.encode({
            'type': 'machine', 'id': machine_id,
            'input': ms['input_slots'], 'output': ms['output_slots'],
            'recipe': recipe, 'elapsed': elapsed,
        })

    def _process_trades(self) -> None:
        # spot buy/sell against the shared market, validated + applied on the
        # player's authoritative inventory (the change rides back on the next
        # per-player inv message). market price-impact is still deferred.
        for conn in self.conns.values():
            trade = conn.pending_trade
            conn.pending_trade = None
            if trade is None or conn.dead_until is not None:
                continue
            side, item_id, qty = trade
            if not item_id or qty <= 0:
                continue
            p = self.sim.world.entities.get(conn.player_id)
            if p is None:
                continue
            if side == 'sell':
                self.sim.spot_market.sell(p.inventory, item_id, qty)
            elif side == 'buy':
                self.sim.spot_market.buy(p.inventory, item_id, qty)

    def _auto_pickup(self) -> None:
        # each living player sweeps up drops overlapping their hitbox into their
        # own inventory (first player to reach a drop wins it).
        for conn in self.conns.values():
            if conn.dead_until is not None:
                continue
            p = self.sim.world.entities.get(conn.player_id)
            if p is None:
                continue
            for d in self.sim.world.collect_dropped_in_rect(p.hitbox_rect()):
                leftover = p.inventory.add_item(d.item_id, d.quantity)
                if leftover > 0:
                    self.sim.world.spawn_dropped_item(d.item_id, leftover, d.world_pos)

    def _handle_deaths(self) -> None:
        # paced death: when a player hits 0 hp, freeze them (hp stays 0, intents
        # ignored) for DEATH_SCREEN_SEC so the client can show YOU DIED, then
        # drop their loot + recenter at full health.
        for conn in self.conns.values():
            p = self.sim.world.entities.get(conn.player_id)
            if p is None:
                continue
            if conn.dead_until is None:
                if p.health is not None and p.health <= 0:
                    conn.dead_until = self._elapsed + DEATH_SCREEN_SEC
            elif self._elapsed >= conn.dead_until:
                self._respawn(p)
                conn.dead_until = None

    def _drop_player_goods(self, p, include_dropbox: bool = False) -> None:
        # spill a player's inventory + held cursor onto the ground at their feet.
        # on disconnect we also spill their drop box (include_dropbox=True) so
        # staged deposits aren't silently lost when the per-connection player is
        # removed; on death we keep the drop box (it's a bank-like staging area).
        at = p.center
        for slot in p.inventory.slots:
            if slot is not None:
                self.sim.world.spawn_dropped_item(slot['item_id'], slot['quantity'], at)
        if p.held_item is not None:
            self.sim.world.spawn_dropped_item(p.held_item['item_id'], p.held_item['quantity'], at)
        if include_dropbox and p.exchange_state is not None:
            for slot in p.exchange_state['drop_box']:
                if slot is not None:
                    self.sim.world.spawn_dropped_item(slot['item_id'], slot['quantity'], at)

    def _respawn(self, player) -> None:
        w = self.sim.world
        sw, sh = player.sprite_dims
        self._drop_player_goods(player)
        player.inventory.slots = [None] * len(player.inventory.slots)
        player.held_item = None
        player.world_x = w.width * TILE_LENGTH / 2 - sw / 2
        player.world_y = w.height * TILE_LENGTH / 2 - sh / 2
        player.health = player.max_health
        player.knockback_x = player.knockback_y = 0.0
        player.path = []

    async def _tick_loop(self) -> None:
        # one bad client packet must never take down the shared sim, so the whole
        # tick body is guarded — a failed tick logs and the world keeps running.
        dt = 1.0 / TICK_HZ
        while True:
            try:
                self._tick(dt)
            except Exception:
                traceback.print_exc()
            await asyncio.sleep(dt)

    def _tick(self, dt: float) -> None:
        self._elapsed += dt
        for conn in self.conns.values():
            conn.attack_cd = max(0.0, conn.attack_cd - dt)
        self._process_attacks()
        self._process_breaks()
        self._process_trades()
        self._process_player_actions()
        self._process_places()
        self._apply_move_intents(dt)
        self.sim.tick(dt)
        self._auto_pickup()
        self._handle_deaths()
        snap = netproto.encode({
            'type': 'snapshot',
            'ents': netproto.entity_snapshot(self.sim.world),
            'dropped': netproto.dropped_snapshot(self.sim.world),
            'overlay': [list(t) for t in self._overlay_changes],   # cleared-tile deltas
            'prices': dict(self.sim.spot_market.prices),
            'day_elapsed': self.sim.day_clock.elapsed,   # client derives the day number
        })
        self._overlay_changes.clear()
        for conn in list(self.conns.values()):
            self._send_to(conn, snap)
        self._save_accum += dt
        if self._save_accum >= SAVE_INTERVAL:
            self._save_accum = 0.0
            self.sim.save()   # synchronous: RLE keeps it fast; threading it would race the sim
            print('[server] autosaved')

    def _send_to(self, conn, snap) -> None:
        w = conn.writer
        try:
            # drop a client that has stopped reading (e.g. its window is being
            # dragged) before its send buffer grows without bound.
            if w.transport is None or w.transport.get_write_buffer_size() > MAX_WRITE_BUFFER:
                w.close()   # handle_client's reader unblocks and runs cleanup
                return
            w.write(snap)   # shared world snapshot (not awaiting drain)
            p = self.sim.world.entities.get(conn.player_id)
            if p is not None:
                # per-player inventory + held cursor (private to each client)
                w.write(netproto.encode({'type': 'inv', 'slots': p.inventory.slots, 'held': p.held_item}))
                # per-player forward-contract state (board / active / drop box)
                w.write(netproto.encode({'type': 'exchange', 'state': p.exchange_state}))
            if conn.open_machine is not None:
                mm = self._machine_msg(conn.open_machine)
                if mm is not None:
                    w.write(mm)
        except (ConnectionError, OSError):
            pass   # client went away mid-send; its reader task runs cleanup
        except Exception:
            traceback.print_exc()   # a real bug (serialization, etc.) — log, don't swallow

    async def run(self, host: str = '127.0.0.1', port: int = 5555) -> None:
        srv = await asyncio.start_server(self.handle_client, host, port)
        print(f'[server] listening on {host}:{port}  (tick {TICK_HZ}Hz)')
        async with srv:
            await asyncio.gather(srv.serve_forever(), self._tick_loop())


def main() -> None:
    server = GameServer()
    host = os.environ.get('HOST', '0.0.0.0')   # bind all interfaces (cloud-reachable)
    port = int(os.environ.get('PORT', '5555'))
    try:
        asyncio.run(server.run(host, port))
    except KeyboardInterrupt:
        print('\n[server] shutting down')
    finally:
        server.sim.save()
        print('[server] world saved')


if __name__ == '__main__':
    main()
