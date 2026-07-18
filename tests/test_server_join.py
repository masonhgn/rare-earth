
# tests for the authoritative server's join path.
#
# 1) a real-socket smoke test: start the actual GameServer on an ephemeral port
#    and confirm a client gets the welcome and a stream of snapshots.
#
# 2) a deterministic regression guard for the "connect drops instantly on a big
#    world" bug. the server used to add a connection to its broadcast set BEFORE
#    the multi-megabyte join snapshot finished sending, so the tick loop's
#    per-client write-buffer guard saw the still-draining welcome exceed
#    MAX_WRITE_BUFFER and closed the client mid-join. it never reproduced on
#    localhost (loopback has no bandwidth limit and the OS absorbs the whole
#    welcome instantly), so instead of a flaky network-timing test we assert the
#    fix's INVARIANT directly with a fake reader/writer: the connection must not
#    enter server.conns until the welcome has drained.

import asyncio
import json
import os
import socket
import struct
import sys
import threading
import tempfile
import time

import world as world_mod
import save_state
import server as server_mod
import netproto


def _make_server(dim):
    # a fresh GameServer seeded with a `dim` x `dim` world. point the save path
    # at a nonexistent file so load() fails and it seeds, and set the dims
    # world.py reads at World construction.
    save_state.SERVER_SAVE_PATH = os.path.join(tempfile.gettempdir(), '_reearth_test_nosave.json')
    try:
        os.remove(save_state.SERVER_SAVE_PATH)
    except OSError:
        pass
    world_mod.WORLD_WIDTH = dim
    world_mod.WORLD_HEIGHT = dim
    return server_mod.GameServer()


# --- 1) real-socket smoke test -------------------------------------------

def _recv_exactly(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def _recv_msg(sock):
    header = _recv_exactly(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack('!I', header)
    body = _recv_exactly(sock, length)
    if body is None:
        return None
    return json.loads(body.decode('utf-8'))


class _NetServer:
    # runs a real GameServer on 127.0.0.1:<ephemeral> in a background thread.
    def __init__(self, dim):
        self.gs = _make_server(dim)
        self.port = None
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        assert self._ready.wait(30), 'server failed to start'

    def _run(self):
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._serve())

    async def _serve(self):
        srv = await asyncio.start_server(self.gs.handle_client, '127.0.0.1', 0)
        self.port = srv.sockets[0].getsockname()[1]
        self._ready.set()
        async with srv:
            await asyncio.gather(srv.serve_forever(), self.gs._tick_loop())


def _recv_join(s):
    # welcome + the streamed map chunks; returns (welcome, reassembled grids).
    welcome = _recv_msg(s)
    assert welcome is not None and welcome['type'] == 'welcome'
    wd = welcome['world']
    w, h = wd['width'], wd['height']
    map_grid = [['grass'] * w for _ in range(h)]
    overlay_grid = [[None] * w for _ in range(h)]
    for _ in range(wd['n_chunks']):
        chunk = _recv_msg(s)
        assert chunk is not None and chunk['type'] == 'chunk', 'map transfer interrupted'
        netproto.apply_chunk(map_grid, overlay_grid, chunk)
    return welcome, map_grid, overlay_grid


def test_client_joins_and_receives_snapshots():
    srv = _NetServer(dim=60)
    s = socket.create_connection(('127.0.0.1', srv.port), timeout=15)
    s.settimeout(15)
    try:
        welcome, _, _ = _recv_join(s)
        assert 'player_id' in welcome
        assert set(welcome['world']) >= {'width', 'height', 'chunk_size', 'n_chunks', 'ents'}

        snapshots = 0
        for _ in range(80):
            msg = _recv_msg(s)
            assert msg is not None, 'server dropped the client unexpectedly'
            if msg['type'] == 'snapshot':
                snapshots += 1
                if snapshots >= 5:
                    break
        assert snapshots >= 5, f'only received {snapshots} snapshots'
    finally:
        s.close()


def test_socket_transport_connect_send_poll():
    # the client-side Transport seam driven against a real server: connect()
    # must return the join payload, poll() must yield snapshots (carrying the
    # events channel), and send() must reach the server without raising.
    import time
    from transport import SocketTransport

    srv = _NetServer(dim=60)
    t = SocketTransport('127.0.0.1', srv.port)
    try:
        join = t.connect()
        assert set(join) == {'player_id', 'world', 'map_grid', 'overlay_grid'}
        assert len(join['map_grid']) == 60 and len(join['map_grid'][0]) == 60
        assert t.alive()

        # an intent must serialize + reach the server without error.
        t.send({'type': 'move', 'dx': 1, 'dy': 0})

        # poll a few times; snapshots should accumulate and carry an events key.
        snaps = []
        for _ in range(40):
            for msg in t.poll():
                if msg.get('type') == 'snapshot':
                    snaps.append(msg)
            if len(snaps) >= 5:
                break
            time.sleep(0.02)
        assert len(snaps) >= 5, f'only polled {len(snaps)} snapshots'
        assert all('events' in s for s in snaps)
    finally:
        t.close()
    assert not t.alive()


def test_client_pumps_frames_against_a_live_server():
    # the whole refactored Client loop, headless (SDL dummy driver from conftest):
    # build from the join payload, then step real frames — input->intents, inbound
    # apply, predict/interp, render — and confirm it stays alive with a synced
    # local player. exercises the Phase-2 client-loop seam end to end.
    import time
    from transport import SocketTransport
    from client import Client

    srv = _NetServer(dim=60)
    transport = SocketTransport('127.0.0.1', srv.port)
    c = Client(transport, transport.connect())
    try:
        # send a movement intent, then pump frames so snapshots arrive + apply.
        c.transport.send({'type': 'move', 'dx': 1, 'dy': 0})
        alive = True
        for _ in range(30):
            alive = c.step_frame()
            if not alive:
                break
            time.sleep(0.005)
        assert alive, 'client stopped running unexpectedly'
        assert c.world.entities.get(c.local_id) is not None, 'local player never synced'
    finally:
        transport.close()


def test_streamed_chunks_reassemble_to_the_server_map():
    # every chunk frame must be well under the message cap, and reassembling
    # them must reproduce the server's actual grids exactly.
    srv = _NetServer(dim=200)   # 200/64 -> a 4x4 chunk grid with ragged edges
    s = socket.create_connection(('127.0.0.1', srv.port), timeout=15)
    s.settimeout(15)
    try:
        welcome, map_grid, overlay_grid = _recv_join(s)
        assert welcome['world']['n_chunks'] == 4 * 4
        assert map_grid == srv.gs.sim.world.map_grid
        assert overlay_grid == srv.gs.sim.world.overlay_grid
    finally:
        s.close()


def test_chunk_frames_stay_under_the_message_cap():
    import netproto as _np
    srv = _NetServer(dim=200)
    frames = list(_np.map_chunks(srv.gs.sim.world))
    assert frames, 'no chunks produced'
    assert max(len(f) for f in frames) < _np.MAX_MSG_BYTES


# --- 2) deterministic invariant regression guard -------------------------

class _FakeReader:
    # netproto.read_msg awaits readexactly(4); block there until released, then
    # raise IncompleteReadError so read_msg returns None and handle_client's
    # intent loop exits cleanly (simulating the client disconnecting).
    def __init__(self):
        self._release = asyncio.Event()

    def release(self):
        self._release.set()

    async def readexactly(self, n):
        await self._release.wait()
        raise asyncio.IncompleteReadError(partial=b'', expected=n)


class _FakeWriter:
    # captures written bytes; drain() blocks on a gate we control, standing in
    # for a slow client that hasn't acked the welcome yet.
    def __init__(self, drain_gate):
        self.drain_gate = drain_gate
        self.written = bytearray()
        self.closed = False

    def write(self, data):
        self.written += data

    async def drain(self):
        await self.drain_gate.wait()

    def get_extra_info(self, key):
        return ('test', 0)

    def close(self):
        self.closed = True


def test_connection_not_registered_until_welcome_drains():
    gs = _make_server(dim=60)

    async def scenario():
        drain_gate = asyncio.Event()
        reader = _FakeReader()
        writer = _FakeWriter(drain_gate)
        task = asyncio.ensure_future(gs.handle_client(reader, writer))

        # let handle_client run up to `await writer.drain()` (which is gated).
        for _ in range(10):
            await asyncio.sleep(0)

        # THE INVARIANT: the welcome has been written, but because it hasn't
        # drained yet the connection must NOT be in the broadcast set — so the
        # tick loop's write-buffer guard can never fire on the in-flight welcome.
        assert writer.written, 'welcome was not written'
        assert len(gs.conns) == 0, 'connection registered before welcome drained (the bug)'

        # once the welcome drains, the client joins the broadcast set.
        drain_gate.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(gs.conns) == 1, 'connection not registered after welcome drained'

        # client disconnects -> handle_client unwinds and deregisters.
        reader.release()
        await asyncio.wait_for(task, timeout=5)
        assert len(gs.conns) == 0, 'connection not cleaned up on disconnect'

    asyncio.run(scenario())
