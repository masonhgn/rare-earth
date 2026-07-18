
# client-side transport seam.
#
# the client talks to the authoritative world through a Transport: it SENDS
# command intents (move/attack/break/place/trade/...) and POLLS inbound messages
# (the shared snapshot plus per-player inv/exchange/skills/machine). the message
# dicts are the api between transport and client — identical whether they arrived
# off a socket or were synthesized in-process — so the client's apply logic is
# transport-agnostic.
#
# SocketTransport is the networked implementation over a TCP socket. a future
# LocalTransport will drive an in-process SimCore, letting single-player run the
# exact same client loop (the listen-server model) instead of its own.

import queue
import socket
import threading

import pygame as pg

import netproto
from simcore import SimCore
from world_host import WorldHost, Connection
from contracts import ensure_board
from save_state import save_game, load_game


def _recv_map(sock, wd):
    # reassemble the full map from the streamed 'chunk' frames that follow the
    # welcome. blocks until all n_chunks arrive; raises on disconnect. grids are
    # pre-filled so a dropped frame would leave a visible hole rather than crash.
    w, h = wd['width'], wd['height']
    map_grid = [['grass'] * w for _ in range(h)]
    overlay_grid = [[None] * w for _ in range(h)]
    remaining = wd['n_chunks']
    while remaining > 0:
        msg = netproto.recv(sock)
        if msg is None:
            raise ConnectionError('server closed during map transfer')
        if msg.get('type') != 'chunk':
            continue
        netproto.apply_chunk(map_grid, overlay_grid, msg)
        remaining -= 1
    return map_grid, overlay_grid


class SocketTransport:
    # networked transport: a background thread drains inbound frames into a queue
    # the client polls, and send() fires intents. a dead socket flips `_alive`
    # rather than raising per-send, so the client loop only checks alive().
    def __init__(self, host: str = '127.0.0.1', port: int = 5555) -> None:
        self.host = host
        self.port = port
        self.sock = None
        self._incoming: queue.Queue = queue.Queue()
        self._alive = False

    def connect(self) -> dict:
        # connect, then read the welcome + streamed map SYNCHRONOUSLY — before the
        # background reader starts — so chunk frames aren't stolen into the queue.
        # returns the join payload {player_id, world, map_grid, overlay_grid};
        # raises ConnectionError on any failure (caller prints + bails).
        try:
            self.sock = socket.create_connection((self.host, self.port))
        except OSError as exc:
            raise ConnectionError(
                f'could not connect to {self.host}:{self.port} ({exc}). is the server running?'
            )
        welcome = netproto.recv(self.sock)
        if welcome is None or welcome.get('type') != 'welcome':
            raise ConnectionError('no welcome from server')
        wd = welcome['world']
        try:
            map_grid, overlay_grid = _recv_map(self.sock, wd)
        except (ConnectionError, OSError):
            raise ConnectionError('lost the server during the map transfer')
        self._alive = True
        threading.Thread(target=self._read_loop, daemon=True).start()
        return {
            'player_id': welcome['player_id'],
            'world': wd,
            'map_grid': map_grid,
            'overlay_grid': overlay_grid,
        }

    def _read_loop(self) -> None:
        # drain frames into the queue until any exit (EOF/reset/malformed), which
        # flags the transport dead so the client stops rendering stale state.
        try:
            while True:
                msg = netproto.recv(self.sock)
                if msg is None:
                    break
                self._incoming.put(msg)
        except Exception:
            pass
        self._alive = False

    def send(self, msg: dict) -> None:
        # fire one command intent. errors just flip _alive; the loop checks it.
        if not self._alive:
            return
        try:
            netproto.send(self.sock, msg)
        except OSError:
            self._alive = False

    def poll(self) -> list:
        # every inbound message received since the last poll, in arrival order.
        msgs = []
        try:
            while True:
                msgs.append(self._incoming.get_nowait())
        except queue.Empty:
            pass
        return msgs

    def alive(self) -> bool:
        return self._alive

    def close(self) -> None:
        self._alive = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass


class LocalTransport:
    # single-player as a listen server: owns a SimCore + WorldHost with one local
    # player, applies the client's intents straight into the host, and ticks +
    # snapshots the sim each poll(). it produces the exact same message shapes as
    # SocketTransport, so the Client is identical in both modes — the only
    # difference is zero latency and no serialization.
    #
    # it also duck-types the save_game/load_game interface (world / day_clock /
    # spot_market / inventory / save_path / world_name), so single-player uses the
    # same save format it always has.

    def __init__(self, save_path: str | None = None, world_name: str | None = None) -> None:
        self.save_path = save_path
        self.world_name = world_name
        self.sim = None
        self.host = None
        self.conn = None
        self._clock = None
        self._alive = False

    # --- save_game/load_game duck-typed interface (mirrors game.Game) ---

    @property
    def world(self):
        return self.sim.world

    @property
    def spot_market(self):
        return self.sim.spot_market

    @property
    def inventory(self):
        # save_game reads .slots; the local player's item store lives here.
        return self.sim.world.get_player().inventory

    @property
    def day_clock(self):
        return self.sim.day_clock

    @day_clock.setter
    def day_clock(self, value):
        # load_game (via _restore_market_state) swaps in a clock anchored to the
        # saved elapsed seconds; keep it on the sim so the tick advances it.
        self.sim.day_clock = value

    # --- transport interface ---

    def connect(self) -> dict:
        # build the world (load the save slot if present, else seed fresh — either
        # way World() has already spawned the fixed-id 'player'), then return the
        # join payload. grids are handed over BY REFERENCE (load_grids stores the
        # ref), so the client mirror shares the sim's map instead of copying 1M
        # tiles; the client only ever writes idempotent overlay clears to it.
        self.sim = SimCore(seed_default=False)
        self.host = WorldHost(self.sim)
        if not load_game(self, self.save_path):
            self.sim.seed()
        # load_game replaces the day clock, so re-bind the rollover hook (settle
        # contracts + grow crops + autosave), mirroring single-player.
        self.sim.day_clock.on_rollover = self._on_day_rollover
        player = self.sim.world.get_player()
        ensure_board(player.exchange_state, self.sim.spot_market)
        # a single local player; its id is the fixed 'player' the save format uses.
        self.conn = Connection(None, player.id)
        self.host.register('local', self.conn)
        self._clock = pg.time.Clock()
        self._alive = True
        return {
            'player_id': player.id,
            'world': netproto.world_join(self.sim.world, self.sim.spot_market, self.sim.day_clock),
            'map_grid': self.sim.world.map_grid,
            'overlay_grid': self.sim.world.overlay_grid,
        }

    def _on_day_rollover(self, new_day: int) -> None:
        self.sim._on_day_rollover(new_day)   # settle contracts + grow crops
        save_game(self, self.save_path)      # autosave on the day boundary

    def admin_commands(self) -> dict:
        # dev-console command table bound to this sim. its presence is what tells
        # the Client to offer a console at all — single-player only, since these
        # mutate the authoritative world directly (SocketTransport has no such
        # method, so a networked client gets no console).
        import dev_commands
        return dev_commands.make(self.sim)

    def send(self, msg: dict) -> None:
        # apply the intent straight into the host (no wire, no queue).
        self.host.apply_intent(self.conn, msg)

    def poll(self) -> list:
        # advance the sim one frame, then hand back the same messages the network
        # client drains: the shared snapshot followed by this player's private
        # inv/exchange/skills/machine. dt is capped so a stall can't tunnel.
        dt = min(self._clock.tick() / 1000.0, 0.1)
        self.host.tick(dt)
        return [self.host.snapshot_msg(), *self.host.player_msgs(self.conn)]

    def alive(self) -> bool:
        return self._alive

    def close(self) -> None:
        # save on the way out (matches single-player's save-on-quit).
        if self._alive:
            save_game(self, self.save_path)
        self._alive = False
