
# world container: tile map + entities + dropped items.
#
# four collections coexist:
#   map_grid  : 2d list of sprite_ids, the static terrain layer
#   entities  : dict[id -> Entity]; includes player + placed entities
#   tile_index: dict[(tx, ty) -> entity_id] for O(1) tile occupancy lookup,
#               only populated for tile_locked entities
#   dropped   : list[DroppedItem]; loose items on the ground
#   spatial_grid: dict[(gx, gy) -> [DroppedItem,...]] for fast proximity
#                 queries (pickup, stacking).
#
# the tile_index follows the design in tickets/place-and-break.md (ticket 1).
# the dropped-item spatial grid is the chud pattern adapted to keep our
# DroppedItem instances as the units of partitioning.

import math
import random
import pygame as pg

from config import TILE_LENGTH, PLAYER_SPAWN, PLAYER_REACH_TILES, ITEM_STACK_DISTANCE, DROPPED_ITEM_SIZE
from entity import Entity
from prototype import load_prototype
from item import DroppedItem, load_item, roll_drops


def world_to_tile(world_pos: tuple[float, float]) -> tuple[int, int]:
    # floor-divide before int() so negative coords land in the correct tile
    # (int(-0.5) == 0 but the tile is -1).
    return (int(world_pos[0] // TILE_LENGTH), int(world_pos[1] // TILE_LENGTH))


def tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_LENGTH + TILE_LENGTH / 2,
            tile[1] * TILE_LENGTH + TILE_LENGTH / 2)


class World:
    def __init__(self):
        self.map_grid: list[list[str]] = []
        self.width = 0
        self.height = 0
        # base terrain is grass with scattered ore patches sprinkled over it.
        # tweak counts/radii to taste; rarer ores should have fewer/smaller patches.
        self.generate_world_map(
            60, 60,
            base_tile='grass',
            patches=[
                ('coal_ore', 8, 5),
                ('copper_ore', 5, 4),
            ],
        )

        self.entities: dict[str, Entity] = {}
        self.tile_index: dict[tuple[int, int], str] = {}

        self.dropped: list[DroppedItem] = []
        # spatial grid for dropped items, cell size = TILE_LENGTH
        self.spatial_grid: dict[tuple[int, int], list[DroppedItem]] = {}

        self.spawn_player()

    # --- map ---

    def generate_world_map(self, width: int, height: int, *, base_tile: str,
                           patches: list[tuple[str, int, int]] | None = None) -> None:
        # two layers: map_grid is the base terrain (always rendered),
        # overlay_grid is a sparse decoration layer of ore/feature tiles
        # whose sprites use alpha to let the base show through between chunks.
        # each `patches` entry is (tile_id, num_patches, max_radius). patch
        # placement is per-cell probabilistic with linear falloff from center
        # so edges are soft rather than hard circles.
        self.width = width
        self.height = height
        self.map_grid = [[base_tile for _ in range(width)] for _ in range(height)]
        self.overlay_grid: list[list[str | None]] = [[None] * width for _ in range(height)]
        for tile_id, count, max_radius in (patches or []):
            for _ in range(count):
                self._scatter_patch(tile_id, max_radius)

    def _scatter_patch(self, tile_id: str, max_radius: int) -> None:
        cx = random.randint(0, self.width - 1)
        cy = random.randint(0, self.height - 1)
        radius = random.randint(max(2, max_radius - 2), max_radius)
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                x, y = cx + dx, cy + dy
                if not (0 <= x < self.width and 0 <= y < self.height):
                    continue
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > radius:
                    continue
                if random.random() < 1 - (dist / radius):
                    self.overlay_grid[y][x] = tile_id

    def in_bounds_tile(self, tx: int, ty: int) -> bool:
        return 0 <= tx < self.width and 0 <= ty < self.height

    def overlay_at(self, tx: int, ty: int) -> str | None:
        # return the overlay sprite_id at the given tile, or None if the tile
        # is empty or out of bounds. bundles the bounds check that three
        # break-path callers were repeating.
        if not self.in_bounds_tile(tx, ty):
            return None
        return self.overlay_grid[ty][tx]

    def tile_in_reach(self, tx: int, ty: int) -> bool:
        # measure reach from the player's *visual center*, not the sprite
        # top-left. for a 128x128 player sprite at world_x=200, the body is
        # centered around (264, 264) ≈ tile (4, 4), not (3, 3) as the raw
        # world_x would suggest.
        player = self.get_player()
        sprite_w, sprite_h = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        cx = player.world_x + sprite_w / 2
        cy = player.world_y + sprite_h / 2
        ptx = int(cx // TILE_LENGTH)
        pty = int(cy // TILE_LENGTH)
        return abs(tx - ptx) <= PLAYER_REACH_TILES and abs(ty - pty) <= PLAYER_REACH_TILES

    # --- entities ---

    def add_entity(self, entity: Entity) -> None:
        # two-pass collision check for multi-tile entities: validate all
        # footprint tiles are free before writing any, so a partial write
        # never corrupts tile_index on overlap.
        if entity.prototype.tile_locked:
            footprint = entity.footprint()
            for tile in footprint:
                if tile in self.tile_index:
                    raise ValueError(f'tile {tile} already occupied')
            for tile in footprint:
                self.tile_index[tile] = entity.id
        self.entities[entity.id] = entity

    def remove_entity(self, entity_id: str) -> Entity | None:
        # pop-based to make double-remove a noop instead of a KeyError.
        entity = self.entities.pop(entity_id, None)
        if entity is None:
            return None
        if entity.prototype.tile_locked:
            for tile in entity.footprint():
                self.tile_index.pop(tile, None)
        return entity

    def get_entity_at_tile(self, tx: int, ty: int) -> Entity | None:
        eid = self.tile_index.get((tx, ty))
        if eid is None:
            return None
        return self.entities.get(eid)

    def spawn_player(self) -> None:
        proto = load_prototype('player')
        self.add_entity(Entity(proto, PLAYER_SPAWN, entity_id='player'))

    def get_player(self) -> Entity:
        return self.entities['player']

    # --- dropped items + spatial grid ---

    def _grid_key(self, world_pos: tuple[float, float]) -> tuple[int, int]:
        # floor-divide first; see entity.tile_coord for the negative-coord case
        return (int(world_pos[0] // TILE_LENGTH), int(world_pos[1] // TILE_LENGTH))

    def _grid_insert(self, item: DroppedItem) -> None:
        key = self._grid_key(item.world_pos)
        self.spatial_grid.setdefault(key, []).append(item)

    def _rebuild_grid(self) -> None:
        self.spatial_grid.clear()
        for item in self.dropped:
            self._grid_insert(item)

    def _nearby_keys(self, world_pos: tuple[float, float], radius_px: float):
        cx, cy = self._grid_key(world_pos)
        cell_radius = math.ceil(radius_px / TILE_LENGTH)
        for dx in range(-cell_radius, cell_radius + 1):
            for dy in range(-cell_radius, cell_radius + 1):
                yield (cx + dx, cy + dy)

    def spawn_dropped_item(self, item_id: str, quantity: int, world_pos: tuple[float, float]) -> None:
        # validate the item exists so a typo in drops fails loud
        load_item(item_id)
        item = DroppedItem(item_id=item_id, quantity=quantity, world_x=world_pos[0], world_y=world_pos[1])
        self.dropped.append(item)
        self._grid_insert(item)
        self.stack_nearby_dropped()

    def stack_nearby_dropped(self) -> None:
        # merge same-id drops within ITEM_STACK_DISTANCE. uses the spatial
        # grid to only consider neighbors of each cell, not all pairs.
        #
        # we identify "the same drop" by python id(), not by list index or
        # equality. two DroppedItems with equal field values would be `==`
        # under @dataclass(eq=True), so .index(other) could silently return
        # the wrong index and cause the survivor to be removed instead of
        # the duplicate — losing quantity.
        removed_ids: set[int] = set()
        for item in self.dropped:
            if id(item) in removed_ids:
                continue
            for key in self._nearby_keys(item.world_pos, ITEM_STACK_DISTANCE):
                bucket = self.spatial_grid.get(key)
                if not bucket:
                    continue
                for other in bucket:
                    if other is item:
                        continue
                    if id(other) in removed_ids:
                        continue
                    if other.item_id != item.item_id:
                        continue
                    dx = other.world_x - item.world_x
                    dy = other.world_y - item.world_y
                    if dx * dx + dy * dy <= ITEM_STACK_DISTANCE * ITEM_STACK_DISTANCE:
                        item.quantity += other.quantity
                        item.world_x = (item.world_x + other.world_x) / 2
                        item.world_y = (item.world_y + other.world_y) / 2
                        removed_ids.add(id(other))
        if removed_ids:
            self.dropped = [d for d in self.dropped if id(d) not in removed_ids]
            self._rebuild_grid()

    def collect_dropped_in_rect(self, rect: pg.Rect) -> list[DroppedItem]:
        # rectangle-based pickup: drop's 32x32 rect must overlap with `rect`.
        # spatial grid is queried only over cells that touch the rect (plus
        # one drop-size inflate so a drop in the next cell can still overlap).
        drop_size = DROPPED_ITEM_SIZE
        inflated = rect.inflate(drop_size, drop_size)
        gx0 = int(inflated.left // TILE_LENGTH)
        gy0 = int(inflated.top // TILE_LENGTH)
        gx1 = int(inflated.right // TILE_LENGTH) + 1
        gy1 = int(inflated.bottom // TILE_LENGTH) + 1
        picked: list[DroppedItem] = []
        seen: set[int] = set()
        for gx in range(gx0, gx1):
            for gy in range(gy0, gy1):
                bucket = self.spatial_grid.get((gx, gy))
                if not bucket:
                    continue
                for item in bucket:
                    if id(item) in seen:
                        continue
                    drop_rect = pg.Rect(int(item.world_x), int(item.world_y), drop_size, drop_size)
                    if rect.colliderect(drop_rect):
                        picked.append(item)
                        seen.add(id(item))
        if picked:
            picked_set = {id(p) for p in picked}
            self.dropped = [d for d in self.dropped if id(d) not in picked_set]
            self._rebuild_grid()
        return picked

    # --- break helper: removes an entity, returns its drops ---

    def break_entity(self, entity_id: str) -> list[tuple[str, int]]:
        entity = self.entities.get(entity_id)
        if entity is None or not entity.prototype.editable:
            return []
        drops = roll_drops(entity.prototype.drops)
        self.remove_entity(entity_id)
        return drops
