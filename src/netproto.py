
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
import struct

from save_state import _rle_encode


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


def world_join(world, spot_market, day_clock) -> dict:
    # one-time payload a connecting client needs to build its world: the static
    # map (RLE, like the save) plus the first full snapshot + economy/clock.
    return {
        'width': world.width,
        'height': world.height,
        'map_grid': _rle_encode(world.map_grid),
        'overlay_grid': _rle_encode(world.overlay_grid),
        'ents': entity_snapshot(world),
        'dropped': dropped_snapshot(world),
        'spot_prices': dict(spot_market.prices),
        'day_elapsed': day_clock.elapsed,
    }
