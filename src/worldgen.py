
# default world seeding, shared by the client (Game._seed_world) and the
# headless server (SimCore), so the two can't drift. places the factory,
# exchange + contract boards, a scattering of mobs (goblins, cows, ghosts), and
# a couple of pickups onto a freshly generated World. headless-safe (no
# rendering / display).

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
    # mobs scattered around spawn (the player spawns at ~tile 6,6). each is
    # routed through nearest_walkable so it can't land inside the factory
    # (x10-21,y6-13) or exchange (x4-7,y16-19) footprint. cows are passive
    # ambient wildlife; goblins and ghosts are hostile and will chase/attack.
    mob_placements = [
        ('goblin', (8, 8)),
        ('goblin', (30, 20)),
        ('goblin', (14, 34)),
        ('cow', (20, 4)),
        ('cow', (26, 10)),
        ('cow', (5, 28)),
        ('cow', (34, 30)),
        ('ghost', (2, 22)),
        ('ghost', (24, 26)),
        ('ghost', (36, 12)),
    ]
    for proto_id, (tx, ty) in mob_placements:
        tile = world.nearest_walkable(tx, ty)
        if tile is None:
            continue
        world.add_entity(Entity(
            load_prototype(proto_id),
            (tile[0] * TILE_LENGTH, tile[1] * TILE_LENGTH),
        ))
