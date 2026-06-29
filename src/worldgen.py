
# default world seeding, shared by the client (Game._seed_world) and the
# headless server (SimCore), so the two can't drift. places the factory,
# exchange + contract boards, a starter goblin, and a couple of pickups onto a
# freshly generated World. headless-safe (no rendering / display).

from config import TILE_LENGTH
from entity import Entity
from prototype import load_prototype
from contracts import initial_board


def seed_world(world, spot_market) -> None:
    # factory: 12x8 footprint anchored at tile (10, 6) — inland, east of spawn.
    try:
        world.add_entity(Entity(load_prototype('factory'), (10 * TILE_LENGTH, 6 * TILE_LENGTH)))
    except ValueError:
        pass
    # exchange: 4x4 footprint at tile (4, 16), south of spawn. ValueError
    # swallowed so a random overlay patch clipping the spot doesn't crash boot.
    try:
        world.add_entity(Entity(load_prototype('exchange'), (4 * TILE_LENGTH, 16 * TILE_LENGTH)))
    except ValueError:
        pass
    # fresh contract board on every exchange entity.
    for ent in world.entities_with('exchange'):
        es = ent.components['exchange']
        if not es['board']:
            es['board'] = initial_board(spot_market)
    # a couple of pickups so loot is visible right away.
    world.spawn_dropped_item('coin', 7, (8 * TILE_LENGTH, 6 * TILE_LENGTH))
    world.spawn_dropped_item('copper', 42, (4 * TILE_LENGTH, 6 * TILE_LENGTH))
    # a wandering goblin near spawn, routed through nearest_walkable so it can't
    # land inside the factory (x10-21,y6-13) or exchange (x4-7,y16-19) footprint.
    goblin_tile = world.nearest_walkable(8, 8)
    if goblin_tile is not None:
        world.add_entity(Entity(
            load_prototype('goblin'),
            (goblin_tile[0] * TILE_LENGTH, goblin_tile[1] * TILE_LENGTH),
        ))
