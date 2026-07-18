
# authoritative TCP game server.
#
# thin networking wrapper around WorldHost (world_host.py): it accepts client
# connections (one player entity per connection), streams each the join snapshot
# + map, forwards their validated intents into the host, and broadcasts the
# host's per-tick snapshot + per-player messages. all the sim + command logic
# lives in WorldHost, shared with the single-player listen server. start with:
#   python src/server.py

import asyncio
import os
import sys
import traceback

# run headless — cloud boxes have no display/audio. must be set before pygame
# initializes its subsystems (pg.init in GameServer.__init__).
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import pygame as pg

from simcore import SimCore
from world_host import WorldHost
import netproto
import save_state

# server runtime knobs — env-overridable (like HOST/PORT/MAX_PLAYERS) so ops can
# retune a deployment without editing source. (gameplay balance like the attack
# cooldown lives in WorldHost, shared with single-player.)
TICK_HZ = int(os.environ.get('TICK_HZ', '20'))
SAVE_INTERVAL = float(os.environ.get('SAVE_INTERVAL', '60'))          # sec between autosaves
MAX_WRITE_BUFFER = int(os.environ.get('MAX_WRITE_BUFFER', str(1 << 20)))  # drop a client past this unsent (1 MB)


class GameServer:
    def __init__(self) -> None:
        # pygame is initialized (no display) only because the sim systems still
        # read pg.time.get_ticks() for break/factory/combat timing.
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
        self.host = WorldHost(self.sim)
        self._save_accum = 0.0   # seconds since the last autosave
        self.max_players = int(os.environ.get('MAX_PLAYERS', '16'))

    @property
    def conns(self) -> dict:
        # the broadcast set lives on the host; exposed here for the join-ordering
        # invariant test and any ops introspection.
        return self.host.conns

    # --- per-connection lifecycle ---

    async def handle_client(self, reader, writer) -> None:
        # refuse new players past the cap — each connection is a player entity in
        # the world, so an unbounded flood would exhaust the sim.
        if len(self.host.conns) >= self.max_players:
            writer.close()
            return
        conn = self.host.create_player(writer)
        pid = conn.player_id
        peer = writer.get_extra_info('peername')
        try:
            # send the join snapshot and flush it BEFORE registering the
            # connection in the broadcast set. the welcome + streamed map are
            # megabytes on a big world and take many ticks to reach a remote
            # client; if the connection were already registered, the tick loop's
            # per-client write-buffer guard would see the still-draining welcome
            # exceed MAX_WRITE_BUFFER and drop the player mid-join. only once the
            # join has flushed do we start streaming per-tick snapshots.
            writer.write(netproto.encode({
                'type': 'welcome',
                'player_id': pid,
                'world': netproto.world_join(self.sim.world, self.sim.spot_market, self.sim.day_clock),
            }))
            await writer.drain()
            for frame in netproto.map_chunks(self.sim.world):
                writer.write(frame)
                await writer.drain()
            self.host.register(id(writer), conn)
            print(f'[server] {peer} joined as {pid} ({len(self.host.conns)} online)')
            while True:
                msg = await netproto.read_msg(reader)
                if msg is None:
                    break
                self.host.apply_intent(conn, msg)
        finally:
            was_registered = self.host.remove_player(id(writer), conn)
            try:
                writer.close()
            except Exception:
                pass
            # only a client that actually joined (welcome flushed, registered)
            # logs a "left"; one that dropped mid-welcome never did.
            if was_registered:
                print(f'[server] {pid} left ({len(self.host.conns)} online)')

    # --- authoritative tick + broadcast ---

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
        self.host.tick(dt)
        snap = netproto.encode(self.host.snapshot_msg())
        for conn in list(self.host.conns.values()):
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
            for m in self.host.player_msgs(conn):   # per-player inv/exchange/skills/machine
                w.write(netproto.encode(m))
        except (ConnectionError, OSError):
            pass   # client went away mid-send; its reader task runs cleanup
        except Exception:
            traceback.print_exc()   # a real bug (serialization, etc.) — log, don't swallow

    # --- admin console (stdin) ---

    def _print_help(self) -> None:
        print('[server] commands:  help | newworld | exit')

    def _new_world(self) -> None:
        # dev command: discard the current world (and its save) and generate a
        # brand-new seeded one. connected clients are disconnected — the join
        # flow streams the whole world once at connect, so they must reconnect
        # to see it. runs synchronously between ticks (single-threaded asyncio),
        # so it can't race a tick mid-mutation.
        for conn in list(self.host.conns.values()):
            try:
                conn.writer.close()
            except Exception:
                pass
        self.sim = SimCore(seed_default=False)
        self.sim.seed()
        self.sim.world.remove_entity('player')   # per-connection players only
        self.host = WorldHost(self.sim)          # fresh host: clears conns/elapsed/deltas
        self._save_accum = 0.0
        try:
            os.remove(save_state.SERVER_SAVE_PATH)
        except OSError:
            pass
        self.sim.save()   # persist the fresh world so a restart keeps it
        print('[server] generated a new world; connected players were disconnected')

    async def _console_loop(self) -> None:
        # read admin commands from stdin without blocking the event loop (a
        # blocking readline runs in the default thread executor). EOF (piped /
        # no tty, e.g. a cloud box) just disables the console; the server runs on.
        loop = asyncio.get_running_loop()
        self._print_help()
        while not self._shutdown.is_set():
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                return   # stdin closed: no interactive console, keep serving
            cmd = line.strip().lower()
            if cmd in ('exit', 'quit', 'stop'):
                print('[server] shutting down')
                self._shutdown.set()
                return
            elif cmd in ('newworld', 'regen', 'reset'):
                self._new_world()
            elif cmd in ('help', '?'):
                self._print_help()
            elif cmd:
                print(f"[server] unknown command '{cmd}' (type 'help')")

    async def run(self, host: str = '127.0.0.1', port: int = 5555) -> None:
        self._shutdown = asyncio.Event()
        srv = await asyncio.start_server(self.handle_client, host, port)
        print(f'[server] listening on {host}:{port}  (tick {TICK_HZ}Hz)')
        async with srv:
            tasks = [
                asyncio.create_task(srv.serve_forever()),
                asyncio.create_task(self._tick_loop()),
                asyncio.create_task(self._console_loop()),
            ]
            await self._shutdown.wait()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


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
