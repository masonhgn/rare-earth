
# default world seeding, data-driven from data/worldgen.json and shared by the
# client (Game._seed_world) and the headless server (SimCore) so the two can't
# drift. the config lists terrain patches (read by World.__init__ for the base
# map) plus the fixed entity/pickup/mob placements applied here. headless-safe
# (no rendering / display).

import json

from config import TILE_LENGTH, WORLDGEN_FILE
from entity import Entity
from prototype import load_prototype


_config_cache = None


def load_worldgen_config() -> dict:
    # cached parse of data/worldgen.json. read by World.__init__ (terrain
    # patches) and seed_world (entities / pickups / mobs).
    global _config_cache
    if _config_cache is None:
        with open(WORLDGEN_FILE) as f:
            _config_cache = json.load(f)
    return _config_cache


def seed_world(world) -> None:
    # place the fixed contents onto a freshly generated World. terrain (base
    # tile + ore patches) is already laid down by World.__init__ from the same
    # config; this adds the buildings, starter pickups, and mobs.
    cfg = load_worldgen_config()

    # buildings + props at fixed tiles. ValueError (a random overlay patch
    # clipped the spot) is swallowed so it can't crash boot.
    for spec in cfg.get('entities', []):
        tx, ty = spec['tile']
        try:
            world.add_entity(Entity(load_prototype(spec['proto']),
                                    (tx * TILE_LENGTH, ty * TILE_LENGTH)))
        except ValueError:
            pass

    # starter pickups so loot is visible right away.
    for spec in cfg.get('pickups', []):
        tx, ty = spec['tile']
        world.spawn_dropped_item(spec['item'], spec['quantity'],
                                 (tx * TILE_LENGTH, ty * TILE_LENGTH))

    # mobs, each routed through nearest_walkable so it can't spawn inside a
    # building footprint. cows are passive; goblins/ghosts chase + attack.
    for spec in cfg.get('mobs', []):
        tx, ty = spec['tile']
        tile = world.nearest_walkable(tx, ty)
        if tile is None:
            continue
        world.add_entity(Entity(load_prototype(spec['proto']),
                                (tile[0] * TILE_LENGTH, tile[1] * TILE_LENGTH)))
