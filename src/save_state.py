
# save / load: single-file json snapshot of the entire game state.
#
# captured surfaces:
#   - day clock elapsed seconds
#   - spot market prices
#   - player position
#   - inventory slot contents
#   - world map_grid + overlay_grid, RLE-encoded (both are highly redundant:
#     base terrain is one tile id, the ore overlay is sparse)
#   - placed entities, each with whatever components it carries
#     (machine for factories, exchange for the trading post)
#   - dropped items on the ground
#
# schema versions are migrated forward on load via the MIGRATIONS chain.
# a save written under an older version is read, run through every
# migrator in sequence to today's SCHEMA_VERSION, and applied. saves
# from a version we don't know how to migrate (newer than the current
# code, or older than the oldest registered migrator) are backed up to
# `save.json.v{N}.bak` rather than discarded, so a downgrade or rollback
# doesn't silently destroy player progress.
#
# the file is gzip-compressed json (the repetitive grids shrink ~150x on top
# of RLE). write is atomic: data is dumped to a .tmp file and renamed into
# place, so a mid-write crash never leaves a corrupted save. load auto-detects
# gzip vs plain json, so pre-compression saves still read.

import gzip
import json
import os
import re
from itertools import chain, groupby

import pygame as pg

from clock import DayClock
from config import DAY_LENGTH_SEC
from entity import Entity
from item import DroppedItem
from prototype import load_prototype


SCHEMA_VERSION = 7

# saves live next to the project root, not inside src/. resolves relative
# to this file so the path is stable regardless of cwd.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_DIR = os.path.join(_PROJECT_ROOT, 'saves')
SAVE_PATH = os.path.join(SAVE_DIR, 'save.json')
# named single-player worlds each get their own file here, so the world-select
# screen can list / load / delete them independently of the legacy SAVE_PATH.
WORLDS_DIR = os.path.join(SAVE_DIR, 'worlds')
# the shared multiplayer world persists separately from single-player saves.
# RARE_EARTH_SAVE lets a cloud deploy point this at a mounted persistent volume.
SERVER_SAVE_PATH = os.environ.get('RARE_EARTH_SAVE', os.path.join(SAVE_DIR, 'server.json'))


def save_exists(path: str = SAVE_PATH) -> bool:
    return os.path.isfile(path)


# ---------------------------------------------------------------------------
# named single-player world slots (world-select screen)
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    # filesystem-safe stem from a display name. collapses runs of non
    # [a-z0-9_-] to a single underscore; falls back to 'world' if empty.
    slug = re.sub(r'[^a-z0-9_-]+', '_', name.strip().lower()).strip('_')
    return slug or 'world'


def world_path(name: str) -> str:
    # path a new world with this display name saves to. a brand-new world's
    # file doesn't exist yet, so callers treat "path missing" as "seed fresh".
    return os.path.join(WORLDS_DIR, _slugify(name) + '.json')


def list_worlds() -> list[dict]:
    # metadata for every saved world, newest-played first. each entry:
    #   {'path', 'name' (display), 'day' (in-game day number), 'mtime'}.
    # unreadable/corrupt files are skipped rather than crashing the menu.
    out: list[dict] = []
    if not os.path.isdir(WORLDS_DIR):
        return out
    for fn in os.listdir(WORLDS_DIR):
        if not fn.endswith('.json'):
            continue
        path = os.path.join(WORLDS_DIR, fn)
        try:
            data = _read_save(path)
        except Exception:
            continue
        out.append({
            'path': path,
            'name': data.get('name', fn[:-5]),
            'day': int(data.get('day_elapsed', 0) // DAY_LENGTH_SEC) + 1,
            'mtime': os.path.getmtime(path),
        })
    out.sort(key=lambda wm: wm['mtime'], reverse=True)
    return out


def delete_world(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# grid RLE + gzip file codec
# ---------------------------------------------------------------------------
#
# the two world grids dominate the save (a 1000x1000 map is ~1M cells each),
# but they're hugely redundant: base terrain is a single tile id and the ore
# overlay is sparse. row-major run-length encoding collapses them to a few
# thousand [value, count] runs, which also makes the write fast (we serialize
# runs, not a million cells). the whole json is then gzipped on top.

def _rle_encode(grid: list[list]) -> list:
    return [[val, sum(1 for _ in grp)]
            for val, grp in groupby(chain.from_iterable(grid))]


def _rle_decode(runs: list, width: int, height: int) -> list[list]:
    flat = list(chain.from_iterable([val] * count for val, count in runs))
    return [flat[i * width:(i + 1) * width] for i in range(height)]


def _read_save(path: str) -> dict:
    # new saves are gzipped; pre-compression plain-json saves still load.
    # detect by gzip magic bytes (1f 8b) rather than trusting the extension.
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    return json.loads(raw.decode('utf-8'))


def _write_save(data: dict, path: str) -> None:
    # atomic gzip write: dump to <path>.tmp then rename into place, so a
    # mid-write crash never leaves a half-written save. the RLE grids are still
    # text-repetitive, so gzip adds a large further win for near-zero cost.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def save_game(g, path: str | None = None) -> None:
    # snapshot ordering matches the load path so a round-trip mirrors itself.
    # path resolves to the game's chosen world slot (set by the world-select
    # screen); falls back to the legacy single-slot SAVE_PATH when unset.
    if path is None:
        path = getattr(g, 'save_path', None) or SAVE_PATH
    w = g.world
    player = w.get_player()
    data = {
        'version': SCHEMA_VERSION,
        # display name for the world-select list; falls back to the file stem
        # on load if a legacy save predates this field.
        'name': getattr(g, 'world_name', None),
        'day_elapsed': g.day_clock.elapsed,
        # spot prices are tied to global game state, not per-exchange. walked
        # offsets aren't persisted — the post-load tick resumes from a clean 5s
        # window, fine because individual price steps carry no hidden state.
        'spot_prices': dict(g.spot_market.prices),
        'player': {
            'world_x': player.world_x,
            'world_y': player.world_y,
            # per-player forward-contract state (board/active/drop_box).
            'exchange': player.exchange_state,
        },
        'inventory_slots': g.inventory.slots,
        'world': _serialize_world(w, pg.time.get_ticks()),
    }
    _write_save(data, path)


# --- per-component codecs ---------------------------------------------------
#
# each component type registers a (serialize, apply) pair below. serialize
# turns the live component state into a json-safe dict; apply merges a saved
# dict back into the freshly prototype-initialized component. adding a new
# component type is one entry in _COMPONENT_CODECS — no new branches in the
# (de)serialize loops.

def _ser_machine(state: dict, now_ms: int) -> dict:
    # elapsed_ms is already a plain relative accumulator, so it round-trips
    # as-is — no wall-clock flattening needed.
    return {
        'input_slots': state['input_slots'],
        'output_slots': state['output_slots'],
        'current_recipe': state['current_recipe'],
        'elapsed_ms': state['elapsed_ms'],
    }


def _apply_machine(target: dict, saved: dict, now_ms: int) -> None:
    target['input_slots'] = saved['input_slots']
    target['output_slots'] = saved['output_slots']
    target['current_recipe'] = saved['current_recipe']
    target['elapsed_ms'] = saved.get('elapsed_ms', 0.0)


def _ser_mob(state: dict, now_ms: int) -> dict:
    # mob ai state is transient (wander/chase + cooldown timers) — persist
    # nothing and let it re-init fresh on load. this is also the seam where
    # mob hp will be persisted once health lands.
    return {}


def _apply_mob(target: dict, saved: dict, now_ms: int) -> None:
    # keep the prototype-fresh component; nothing to restore yet.
    pass


_COMPONENT_CODECS = {
    'machine': (_ser_machine, _apply_machine),
    'mob': (_ser_mob, _apply_mob),
}


def _serialize_components(ent, now_ms: int) -> dict:
    out = {}
    for name, state in ent.components.items():
        codec = _COMPONENT_CODECS.get(name)
        # unknown component — store as-is and hope it round-trips.
        out[name] = codec[0](state, now_ms) if codec else state
    return out


def _serialize_world(w, now_ms: int) -> dict:
    # the shared world blob: RLE grids, placed entities, dropped items. the
    # player-owning entity is skipped — single-player restores the player from
    # data['player'] and the server spawns players per-connection, so neither
    # persists it here. used by both save_game and save_world.
    saved_entities = [
        {
            'id': ent.id,
            'prototype_id': ent.prototype.proto_id,
            'world_x': ent.world_x,
            'world_y': ent.world_y,
            'components': _serialize_components(ent, now_ms),
        }
        for ent in w.entities.values() if not ent.is_player
    ]
    return {
        'width': w.width,
        'height': w.height,
        'map_grid': _rle_encode(w.map_grid),
        'overlay_grid': _rle_encode(w.overlay_grid),
        'entities': saved_entities,
        'dropped': [
            {'item_id': d.item_id, 'quantity': d.quantity,
             'world_x': d.world_x, 'world_y': d.world_y}
            for d in w.dropped
        ],
    }


# ---------------------------------------------------------------------------
# load + migrations
# ---------------------------------------------------------------------------

def load_game(g, path: str = SAVE_PATH) -> bool:
    # returns True if a save was found, migrated forward if necessary,
    # and applied. on a version we don't know how to migrate, backs the
    # file up and returns False so the caller falls back to a fresh
    # world without losing the original save.
    if not save_exists(path):
        return False
    data = _read_save(path)

    data = _migrate_forward(data, path)
    if data is None:
        return False

    w = g.world
    now_ms = pg.time.get_ticks()

    # display name for the world-select list. older saves lack it; fall back
    # to the file stem so the world still shows a sensible label.
    g.world_name = data.get('name') or os.path.splitext(os.path.basename(path))[0]

    # rebuild the world (grids, placed entities, dropped items) from the blob.
    _apply_world(w, data['world'], now_ms)

    # respawn the player at the saved position. fixed id 'player' so the rest
    # of the codebase keeps using world.get_player() unchanged. adding it after
    # the placed entities is fine — the player isn't tile-locked and
    # _rebuild_grid only indexes dropped items, so insertion order is moot.
    w.add_entity(Entity(
        load_prototype('player'),
        (data['player']['world_x'], data['player']['world_y']),
        entity_id='player',
    ))

    # restore inventory slots. copy each dict so save/runtime references don't
    # alias. inventory data lives on the local player's 'player' component now.
    g.world.get_player().inventory.slots = [
        None if s is None else dict(s)
        for s in data['inventory_slots']
    ]

    # restore the player's forward-contract state (board/active/drop_box).
    # older saves (pre-v6) lack it; the v5->v6 migration lifts it off the
    # exchange entity, and a truly-fresh board is filled by ensure_board later.
    saved_exchange = data['player'].get('exchange')
    if saved_exchange is not None:
        g.world.get_player().components['player']['exchange'] = saved_exchange

    _restore_market_state(g, data)
    return True


# ---------------------------------------------------------------------------
# server world persistence (no player / inventory — players are per-connection
# with no accounts yet, so only the shared world is saved)
# ---------------------------------------------------------------------------

def save_world(sim, path: str = SERVER_SAVE_PATH) -> None:
    # persist the shared world: grids, placed entities (skipping players),
    # dropped items, spot prices, day clock. mirrors save_game minus the
    # player position + inventory, and reuses the same world codec.
    data = {
        'version': SCHEMA_VERSION,
        'day_elapsed': sim.day_clock.elapsed,
        'spot_prices': dict(sim.spot_market.prices),
        'world': _serialize_world(sim.world, pg.time.get_ticks()),
    }
    _write_save(data, path)


def load_world(sim, path: str = SERVER_SAVE_PATH) -> bool:
    # rebuild sim.world from a save_world file (no player spawned). returns
    # False if there's no save, it's unreadable/corrupt, or the version can't
    # migrate — in every failure case the caller falls back to a fresh seed.
    if not save_exists(path):
        return False
    try:
        raw = _read_save(path)
    except Exception as exc:
        print(f'[server] save unreadable ({exc}); backing it up and starting fresh')
        _backup_save(path, 'corrupt')
        return False
    data = _migrate_forward(raw, path)
    if data is None:
        return False

    _apply_world(sim.world, data['world'], pg.time.get_ticks())
    _restore_market_state(sim, data)
    return True


def _apply_components(ent, saved_components: dict, now_ms: int) -> None:
    # merge saved component state into the freshly initialized component
    # dicts. ent.components is already populated with default shapes by
    # the prototype, so we overwrite per-key rather than wholesale assign.
    for name, saved in saved_components.items():
        target = ent.components.get(name)
        if target is None:
            # save carries a component this prototype no longer defines —
            # silently drop. the prototype is the source of truth.
            continue
        codec = _COMPONENT_CODECS.get(name)
        if codec:
            codec[1](target, saved, now_ms)
        else:
            target.update(saved)


def _apply_world(w, wd: dict, now_ms: int) -> None:
    # wipe the world and repopulate grids + placed entities + dropped items
    # from a saved `world` blob. shared by load_game and load_world. width/height
    # first — the RLE grid decode needs them to reshape rows.
    w.width = wd['width']
    w.height = wd['height']
    w.map_grid = _rle_decode(wd['map_grid'], w.width, w.height)
    w.overlay_grid = _rle_decode(wd['overlay_grid'], w.width, w.height)
    w.entities.clear()
    w.tile_index.clear()
    w.dropped.clear()
    w.spatial_grid.clear()

    # placed entities keep their original ids so future save references stay stable.
    for e_data in wd['entities']:
        ent = Entity(
            load_prototype(e_data['prototype_id']),
            (e_data['world_x'], e_data['world_y']),
            entity_id=e_data['id'],
        )
        _apply_components(ent, e_data.get('components', {}), now_ms)
        w.add_entity(ent)

    # restore dropped items directly rather than via spawn_dropped_item — that
    # would re-run the stacking pass, and the saved positions are already
    # post-stacking. _rebuild_grid then indexes them for pickup queries.
    for d in wd['dropped']:
        w.dropped.append(DroppedItem(
            item_id=d['item_id'], quantity=d['quantity'],
            world_x=d['world_x'], world_y=d['world_y'],
        ))
    w._rebuild_grid()


def _restore_market_state(host, data: dict) -> None:
    # replace the day clock with one anchored to the saved elapsed seconds
    # (caller re-binds on_rollover afterwards), and restore spot prices for any
    # item still tradeable. items that lost their spot_price since the save are
    # silently dropped; newly tradeable items keep their json default. sparkline
    # history is session-local, so reseed it from the restored prices.
    host.day_clock = DayClock(elapsed=data['day_elapsed'])
    saved_prices = data.get('spot_prices', {})
    for item_id in host.spot_market.prices.keys():
        if item_id in saved_prices:
            host.spot_market.prices[item_id] = saved_prices[item_id]
    host.spot_market._tick_clock = 0.0
    host.spot_market.seed_history()


def _migrate_forward(data: dict, path: str) -> dict | None:
    # walk the migration chain until we reach SCHEMA_VERSION or hit an
    # unknown version. on miss, back the file up and bail.
    version = data.get('version')
    while version != SCHEMA_VERSION:
        migrator = MIGRATIONS.get(version)
        if migrator is None:
            _backup_save(path, version)
            return None
        data = migrator(data)
        version = data.get('version')
    return data


def _backup_save(path: str, version) -> None:
    # rename rather than copy so the unknown-version file isn't loaded
    # again next boot. swallow OSError — if we can't rename, the next
    # session will see the same file and back-up loop is idempotent
    # anyway because the original is left in place.
    label = 'unknown' if version is None else f'v{version}'
    backup = f'{path}.{label}.bak'
    try:
        os.replace(path, backup)
    except OSError:
        pass


# --- migrations -------------------------------------------------------------
#
# each migrator takes a v(n) dict and returns a v(n+1) dict. add new
# migrators here; loaders chain forward from the file's version.

def _migrate_v1_to_v2(data: dict) -> dict:
    # v1 stored per-entity state as `machine_state` and `exchange_state`
    # top-level fields. v2 wraps both in `components: {name -> state}`.
    for ent in data.get('world', {}).get('entities', []):
        components = {}
        if 'machine_state' in ent:
            components['machine'] = ent.pop('machine_state')
        if 'exchange_state' in ent:
            components['exchange'] = ent.pop('exchange_state')
        ent['components'] = components
    data['version'] = 2
    return data


def _migrate_v2_to_v3(data: dict) -> dict:
    # rock_chunk was a visual duplicate of copper_ingot and got removed.
    # rewrite every place an item id can appear so a pre-merge save doesn't
    # reference a now-deleted item (which would crash at render time when
    # load_item fails to find rock_chunk.json).
    remap = {'rock_chunk': 'copper_ingot'}

    def fix_slots(slots):
        for s in slots or []:
            if s and s.get('item_id') in remap:
                s['item_id'] = remap[s['item_id']]

    def fix_contract(c):
        if c is None:
            return
        for key in ('deliver_item', 'receive_item'):
            if c.get(key) in remap:
                c[key] = remap[c[key]]

    fix_slots(data.get('inventory_slots', []))
    world = data.get('world', {})
    for d in world.get('dropped', []):
        if d.get('item_id') in remap:
            d['item_id'] = remap[d['item_id']]
    for ent in world.get('entities', []):
        ex = ent.get('components', {}).get('exchange')
        if not ex:
            continue
        fix_slots(ex.get('drop_box', []))
        for c in ex.get('board', []):
            fix_contract(c)
        for c in ex.get('active', []):
            fix_contract(c)
    # drop the stale spot price entry; copper_ingot already carries its own.
    data.get('spot_prices', {}).pop('rock_chunk', None)
    data['version'] = 3
    return data


def _migrate_v3_to_v4(data: dict) -> dict:
    # v4 RLE-encodes the two world grids (they were raw 2D lists in v3) and
    # gzips the file. the file-level gzip is handled transparently by
    # _read_save; here we just convert the grids in-dict so the loader's
    # _rle_decode works uniformly across versions.
    world = data.get('world', {})
    for key in ('map_grid', 'overlay_grid'):
        grid = world.get(key)
        if grid is not None:
            world[key] = _rle_encode(grid)
    data['version'] = 4
    return data


def _migrate_v4_to_v5(data: dict) -> dict:
    # two one-time backfills for saves written before ore-on-stone + ambient
    # mobs landed. both are idempotent-by-version (they run once, on the hop
    # from v4 to v5), so a migrated world keeps whatever the player then does.
    #
    #  1. lay stone under (and in a 1-tile halo around) every existing ore
    #     overlay cell, so deposits generated on the old grass base now read
    #     as rocky outcrops like a freshly generated world.
    #  2. spawn a few cows + ghosts near the saved player position — seed_world
    #     places them in fresh worlds but never runs on load, so an existing
    #     save has none. server saves carry no 'player', so they skip this.
    ORES = {'coal_ore', 'copper_ore', 'iron_ore', 'silver_ore', 'haldrite_ore'}
    world = data.get('world', {})
    w, h = world.get('width', 0), world.get('height', 0)
    if w and h and 'map_grid' in world and 'overlay_grid' in world:
        base = _rle_decode(world['map_grid'], w, h)
        overlay = _rle_decode(world['overlay_grid'], w, h)
        for y in range(h):
            orow = overlay[y]
            for x in range(w):
                if orow[x] in ORES:
                    for ny in range(max(0, y - 1), min(h, y + 2)):
                        brow = base[ny]
                        for nx in range(max(0, x - 1), min(w, x + 2)):
                            if brow[nx] == 'grass':
                                brow[nx] = 'stone'
        world['map_grid'] = _rle_encode(base)

    player = data.get('player') or {}
    px, py = player.get('world_x'), player.get('world_y')
    if px is not None and py is not None:
        ents = world.setdefault('entities', [])
        # (prototype, dx, dy) pixel offsets around the player. an offset that
        # happens to land in a solid (e.g. the factory) is harmless: mobs
        # aren't tile-locked so add_entity accepts them, and separate_living
        # shoves them clear on the first frame.
        backfill = [
            ('cow', 192, 0), ('cow', -192, 64), ('cow', 96, 224),
            ('ghost', 0, -208), ('ghost', -176, -112), ('ghost', 240, 144),
        ]
        for i, (proto, dx, dy) in enumerate(backfill):
            ents.append({
                'id': f'{proto}_backfill_{i}',
                'prototype_id': proto,
                'world_x': px + dx,
                'world_y': py + dy,
                'components': {},
            })

    data['version'] = 5
    return data


def _migrate_v5_to_v6(data: dict) -> dict:
    # forward-contract state (board/active/drop_box) moved off the shared
    # exchange entity onto the (single) player, who now owns it per-player.
    # lift the first exchange entity's state onto the player block and strip
    # the 'exchange' component from every entity. server saves carry no
    # 'player', so their exchange state is simply dropped (players there are
    # per-connection and regenerate a board on join).
    world = data.get('world', {})
    moved = None
    for ent in world.get('entities', []):
        comps = ent.get('components', {})
        if 'exchange' in comps:
            if moved is None:
                moved = comps['exchange']
            del comps['exchange']
    player = data.get('player')
    if player is not None and moved is not None:
        player['exchange'] = moved
    data['version'] = 6
    return data


def _migrate_v6_to_v7(data: dict) -> dict:
    # machine craft progress moved from an absolute started_ms (persisted as
    # craft_elapsed_ms) to a dt-accumulated elapsed_ms. rename the persisted
    # field so an in-progress craft resumes at the right point.
    for ent in data.get('world', {}).get('entities', []):
        m = ent.get('components', {}).get('machine')
        if m is not None and 'craft_elapsed_ms' in m:
            m['elapsed_ms'] = m.pop('craft_elapsed_ms')
    data['version'] = 7
    return data


MIGRATIONS = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
    5: _migrate_v5_to_v6,
    6: _migrate_v6_to_v7,
}
