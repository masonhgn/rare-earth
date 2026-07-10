
# minimal net protocol + world (de)serialization for the authoritative
# server <-> client.
#
# wire format: each message is a 4-byte big-endian length prefix followed by a
# JSON body. JSON is plenty for this world's size; a binary/delta format is a
# later optimization (Phase 4) if bandwidth ever matters.
#
# message types (the full set is authoritative in the code, not here):
#   server -> client: 'welcome' (join snapshot: player_id + world{...}) once on
#     connect; 'snapshot' (ents/dropped/overlay/prices/day_elapsed) every tick;
#     plus 'inv', 'exchange', 'machine' pushed per viewer when they change.
#   client -> server: movement ('move') + intents validated in
#     server.handle_client (break/place/attack/trade/inv_click/drop/accept/
#     cancel/dropbox/machine_click/open_machine/close_machine).

import asyncio
import json
import math
import struct

from save_state import _rle_encode, _rle_decode

# the map is streamed to a joining client as CHUNK_TILES x CHUNK_TILES tiles per
# frame instead of one giant welcome. this keeps any single frame far under
# MAX_MSG_BYTES (a whole 1000x1000 map is ~1.7MB, and >2100^2 would exceed the
# 8MB cap and be unjoinable) and lets the server drain between chunks so a slow
# client never backs the send buffer up.
CHUNK_TILES = 64


def encode(msg: dict) -> bytes:
    body = json.dumps(msg, separators=(',', ':')).encode('utf-8')
    return struct.pack('!I', len(body)) + body


# reject absurd length prefixes (corruption / hostile-peer guard) before
# allocating a buffer for them.
MAX_MSG_BYTES = 8 * 1024 * 1024


async def read_msg(reader) -> dict | None:
    # read one length-prefixed JSON message; None on EOF / disconnect / malformed.
    try:
        header = await reader.readexactly(4)
        (length,) = struct.unpack('!I', header)
        if length <= 0 or length > MAX_MSG_BYTES:
            return None
        body = await reader.readexactly(length)
        return json.loads(body.decode('utf-8'))
    except (asyncio.IncompleteReadError, ConnectionError, ValueError, UnicodeDecodeError):
        return None


# --- blocking socket helpers (client side; the server uses asyncio above) ---

def send(sock, msg: dict) -> None:
    sock.sendall(encode(msg))


def _recv_exactly(sock, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def recv(sock) -> dict | None:
    # blocking length-prefixed read for a plain socket; None on disconnect/malformed.
    try:
        header = _recv_exactly(sock, 4)
        if header is None:
            return None
        (length,) = struct.unpack('!I', header)
        if length <= 0 or length > MAX_MSG_BYTES:
            return None
        body = _recv_exactly(sock, length)
        if body is None:
            return None
        return json.loads(body.decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def entity_snapshot(world) -> list:
    # per-tick dynamic state for every entity — enough for the client to render
    # and reconcile (spawn new ids, update pos/hp, despawn missing ids).
    return [
        {
            'id': e.id,
            'proto': e.prototype.proto_id,
            'x': round(e.world_x, 1),
            'y': round(e.world_y, 1),
            'hp': e.health,
            'mhp': e.max_health,
        }
        for e in world.entities.values()
    ]


def dropped_snapshot(world) -> list:
    return [
        {'item': d.item_id, 'q': d.quantity, 'x': round(d.world_x, 1), 'y': round(d.world_y, 1)}
        for d in world.dropped
    ]


def chunk_grid_dims(width: int, height: int) -> tuple[int, int]:
    # (cols, rows) of CHUNK_TILES-sized chunks covering a width x height map.
    return math.ceil(width / CHUNK_TILES), math.ceil(height / CHUNK_TILES)


def world_join(world, spot_market, day_clock) -> dict:
    # the small welcome a connecting client needs to start building its world:
    # dims + chunk plan + first snapshot + economy/clock. the static map itself
    # follows as a stream of `chunk` frames (see map_chunks).
    cols, rows = chunk_grid_dims(world.width, world.height)
    return {
        'width': world.width,
        'height': world.height,
        'chunk_size': CHUNK_TILES,
        'n_chunks': cols * rows,
        'ents': entity_snapshot(world),
        'dropped': dropped_snapshot(world),
        'spot_prices': dict(spot_market.prices),
        'day_elapsed': day_clock.elapsed,
    }


def map_chunks(world):
    # yield one encoded 'chunk' frame per CHUNK_TILES square region of the map,
    # row-major. each carries its own RLE'd base + overlay sub-grids and its
    # actual size (edge chunks are smaller). the client reassembles them into
    # the full grids on join.
    cs = CHUNK_TILES
    cols, rows = chunk_grid_dims(world.width, world.height)
    for cy in range(rows):
        y0 = cy * cs
        ch = min(cs, world.height - y0)
        for cx in range(cols):
            x0 = cx * cs
            cw = min(cs, world.width - x0)
            sub_map = [row[x0:x0 + cw] for row in world.map_grid[y0:y0 + ch]]
            sub_over = [row[x0:x0 + cw] for row in world.overlay_grid[y0:y0 + ch]]
            yield encode({
                'type': 'chunk', 'cx': cx, 'cy': cy, 'w': cw, 'h': ch,
                'map': _rle_encode(sub_map), 'overlay': _rle_encode(sub_over),
            })


def apply_chunk(map_grid, overlay_grid, msg) -> None:
    # paint one received 'chunk' frame into the client's full grids in place.
    cw, ch = msg['w'], msg['h']
    x0, y0 = msg['cx'] * CHUNK_TILES, msg['cy'] * CHUNK_TILES
    sub_map = _rle_decode(msg['map'], cw, ch)
    sub_over = _rle_decode(msg['overlay'], cw, ch)
    for j in range(ch):
        map_grid[y0 + j][x0:x0 + cw] = sub_map[j]
        overlay_grid[y0 + j][x0:x0 + cw] = sub_over[j]
