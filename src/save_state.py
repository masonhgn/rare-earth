
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
from itertools import chain, groupby

import pygame as pg

from clock import DayClock
from entity import Entity
from item import DroppedItem
from prototype import load_prototype


SCHEMA_VERSION = 4

# saves live next to the project root, not inside src/. resolves relative
# to this file so the path is stable regardless of cwd.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_DIR = os.path.join(_PROJECT_ROOT, 'saves')
SAVE_PATH = os.path.join(SAVE_DIR, 'save.json')
# the shared multiplayer world persists separately from single-player saves.
# RARE_EARTH_SAVE lets a cloud deploy point this at a mounted persistent volume.
SERVER_SAVE_PATH = os.environ.get('RARE_EARTH_SAVE', os.path.join(SAVE_DIR, 'server.json'))


def save_exists(path: str = SAVE_PATH) -> bool:
    return os.path.isfile(path)


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


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def save_game(g, path: str = SAVE_PATH) -> None:
    # snapshot ordering matches the load path so a round-trip mirrors itself.
    w = g.world
    player = w.get_player()
    now_ms = pg.time.get_ticks()

    saved_entities = []
    for ent in w.entities.values():
        if ent.id == 'player':
            continue
        saved_entities.append({
            'id': ent.id,
            'prototype_id': ent.prototype.proto_id,
            'world_x': ent.world_x,
            'world_y': ent.world_y,
            'components': _serialize_components(ent, now_ms),
        })

    data = {
        'version': SCHEMA_VERSION,
        'day_elapsed': g.day_clock.elapsed,
        # spot prices are tied to global game state, not per-exchange.
        # walked offsets aren't persisted — the post-load tick resumes
        # from a clean 5s window, which is fine because individual price
        # steps don't carry hidden state.
        'spot_prices': dict(g.spot_market.prices),
        'player': {
            'world_x': player.world_x,
            'world_y': player.world_y,
        },
        'inventory_slots': g.inventory.slots,
        'world': {
            'width': w.width,
            'height': w.height,
            'map_grid': _rle_encode(w.map_grid),
            'overlay_grid': _rle_encode(w.overlay_grid),
            'entities': saved_entities,
            'dropped': [
                {
                    'item_id': d.item_id,
                    'quantity': d.quantity,
                    'world_x': d.world_x,
                    'world_y': d.world_y,
                }
                for d in w.dropped
            ],
        },
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    # gzip the json — the RLE grids are still text-repetitive, so gzip adds a
    # large further win for near-zero cost. atomic via tmp + replace.
    with gzip.open(tmp, 'wt', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    os.replace(tmp, path)


# --- per-component codecs ---------------------------------------------------
#
# each component type registers a (serialize, apply) pair below. serialize
# turns the live component state into a json-safe dict; apply merges a saved
# dict back into the freshly prototype-initialized component. adding a new
# component type is one entry in _COMPONENT_CODECS — no new branches in the
# (de)serialize loops.

def _ser_machine(state: dict, now_ms: int) -> dict:
    # flatten wall-clock progress to elapsed-since-start so an in-progress
    # craft resumes from the same offset after a restart.
    craft_elapsed_ms = 0
    if state['current_recipe'] is not None:
        craft_elapsed_ms = max(0, now_ms - state['started_ms'])
    return {
        'input_slots': state['input_slots'],
        'output_slots': state['output_slots'],
        'current_recipe': state['current_recipe'],
        'craft_elapsed_ms': craft_elapsed_ms,
    }


def _apply_machine(target: dict, saved: dict, now_ms: int) -> None:
    target['input_slots'] = saved['input_slots']
    target['output_slots'] = saved['output_slots']
    target['current_recipe'] = saved['current_recipe']
    # rebuild started_ms relative to current ticks so crafting progress
    # resumes from the same offset it was paused at.
    target['started_ms'] = now_ms - saved.get('craft_elapsed_ms', 0)


def _ser_exchange(state: dict, now_ms: int) -> dict:
    # already pure plain data — copy the persisted keys.
    return {
        'drop_box': state['drop_box'],
        'board': state['board'],
        'active': state['active'],
    }


def _apply_exchange(target: dict, saved: dict, now_ms: int) -> None:
    target['drop_box'] = saved.get('drop_box', target['drop_box'])
    target['board'] = saved.get('board', [])
    target['active'] = saved.get('active', [])


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
    'exchange': (_ser_exchange, _apply_exchange),
    'mob': (_ser_mob, _apply_mob),
}


def _serialize_components(ent, now_ms: int) -> dict:
    out = {}
    for name, state in ent.components.items():
        codec = _COMPONENT_CODECS.get(name)
        # unknown component — store as-is and hope it round-trips.
        out[name] = codec[0](state, now_ms) if codec else state
    return out


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
    wd = data['world']

    # wipe existing world contents before repopulating from the save.
    # width/height first — the RLE grid decode needs them to reshape rows.
    w.width = wd['width']
    w.height = wd['height']
    w.map_grid = _rle_decode(wd['map_grid'], w.width, w.height)
    w.overlay_grid = _rle_decode(wd['overlay_grid'], w.width, w.height)
    w.entities.clear()
    w.tile_index.clear()
    w.dropped.clear()
    w.spatial_grid.clear()

    # respawn the player at the saved position. fixed id 'player' so the
    # rest of the codebase keeps using world.get_player() unchanged.
    player_proto = load_prototype('player')
    player = Entity(
        player_proto,
        (data['player']['world_x'], data['player']['world_y']),
        entity_id='player',
    )
    w.add_entity(player)

    # respawn placed entities with their original ids so any future save
    # references stay stable.
    now_ms = pg.time.get_ticks()
    for e_data in wd['entities']:
        proto = load_prototype(e_data['prototype_id'])
        ent = Entity(
            proto,
            (e_data['world_x'], e_data['world_y']),
            entity_id=e_data['id'],
        )
        _apply_components(ent, e_data.get('components', {}), now_ms)
        w.add_entity(ent)

    # restore dropped items directly without going through
    # spawn_dropped_item — that would re-run the stacking pass, and the
    # saved positions are already post-stacking.
    for d in wd['dropped']:
        w.dropped.append(DroppedItem(
            item_id=d['item_id'],
            quantity=d['quantity'],
            world_x=d['world_x'],
            world_y=d['world_y'],
        ))
    w._rebuild_grid()

    # restore inventory slots. copy each dict so save/runtime references
    # don't alias.
    # inventory data lives on the local player's 'player' component now.
    g.world.get_player().inventory.slots = [
        None if s is None else dict(s)
        for s in data['inventory_slots']
    ]

    # replace day_clock with a fresh one anchored to the saved elapsed.
    # caller is responsible for re-binding on_rollover after this call.
    g.day_clock = DayClock(elapsed=data['day_elapsed'])

    # restore spot prices for any item still tradeable. items that were
    # tradeable when the save was written but no longer have a spot_price
    # field today are silently dropped; new tradeable items keep their
    # default target_price from the json.
    saved_prices = data.get('spot_prices', {})
    for item_id in g.spot_market.prices.keys():
        if item_id in saved_prices:
            g.spot_market.prices[item_id] = saved_prices[item_id]
    g.spot_market._tick_clock = 0.0
    # restart the sparkline history from the restored prices (history is
    # session-local and not persisted).
    g.spot_market.seed_history()

    return True


# ---------------------------------------------------------------------------
# server world persistence (no player / inventory — players are per-connection
# with no accounts yet, so only the shared world is saved)
# ---------------------------------------------------------------------------

def save_world(sim, path: str = SERVER_SAVE_PATH) -> None:
    # persist the shared world: grids, placed entities (skipping players),
    # dropped items, spot prices, day clock. mirrors save_game minus the
    # player position + inventory, and reuses the same component codecs.
    w = sim.world
    now_ms = pg.time.get_ticks()
    saved_entities = []
    for ent in w.entities.values():
        if ent.is_player:
            continue
        saved_entities.append({
            'id': ent.id,
            'prototype_id': ent.prototype.proto_id,
            'world_x': ent.world_x,
            'world_y': ent.world_y,
            'components': _serialize_components(ent, now_ms),
        })
    data = {
        'version': SCHEMA_VERSION,
        'day_elapsed': sim.day_clock.elapsed,
        'spot_prices': dict(sim.spot_market.prices),
        'world': {
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
        },
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    os.replace(tmp, path)


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

    w = sim.world
    wd = data['world']
    w.width = wd['width']
    w.height = wd['height']
    w.map_grid = _rle_decode(wd['map_grid'], w.width, w.height)
    w.overlay_grid = _rle_decode(wd['overlay_grid'], w.width, w.height)
    w.entities.clear()
    w.tile_index.clear()
    w.dropped.clear()
    w.spatial_grid.clear()

    now_ms = pg.time.get_ticks()
    for e_data in wd['entities']:
        proto = load_prototype(e_data['prototype_id'])
        ent = Entity(proto, (e_data['world_x'], e_data['world_y']), entity_id=e_data['id'])
        _apply_components(ent, e_data.get('components', {}), now_ms)
        w.add_entity(ent)
    for d in wd['dropped']:
        w.dropped.append(DroppedItem(
            item_id=d['item_id'], quantity=d['quantity'],
            world_x=d['world_x'], world_y=d['world_y']))
    w._rebuild_grid()

    sim.day_clock = DayClock(elapsed=data['day_elapsed'])   # caller re-binds on_rollover
    saved_prices = data.get('spot_prices', {})
    for item_id in sim.spot_market.prices.keys():
        if item_id in saved_prices:
            sim.spot_market.prices[item_id] = saved_prices[item_id]
    sim.spot_market._tick_clock = 0.0
    sim.spot_market.seed_history()
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


MIGRATIONS = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
}
