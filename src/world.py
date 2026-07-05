
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

from config import TILE_LENGTH, PLAYER_SPAWN, PLAYER_REACH_TILES, ITEM_STACK_DISTANCE, DROPPED_ITEM_SIZE, WORLD_WIDTH, WORLD_HEIGHT
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


def in_reach(player, tx: int, ty: int, max_dist: int = PLAYER_REACH_TILES) -> bool:
    # is tile (tx, ty) within max_dist tiles of `player`'s visual-center tile?
    # takes the player EXPLICITLY so it works for any player — the local one in
    # single-player, a specific connection's player on the server, or the net
    # client's own player. (World.tile_in_reach is the single-player wrapper
    # that passes the fixed 'player' entity, which only exists in single-player.)
    ptx, pty = player.center_tile
    return abs(tx - ptx) <= max_dist and abs(ty - pty) <= max_dist


class World:
    def __init__(self):
        self.map_grid: list[list[str]] = []
        self.width = 0
        self.height = 0
        # terrain is data-driven (data/worldgen.json): a base tile plus a list
        # of {tile, count, radius} ore patches. patch counts scale with map area
        # so ore density stays ~constant vs the 60x60 baseline. each ore patch
        # lays stone under its cells so ore only ever sits on rock (see
        # _scatter_patch). local import avoids a module-load cycle with worldgen.
        from worldgen import load_worldgen_config
        cfg = load_worldgen_config()
        area_scale = (WORLD_WIDTH * WORLD_HEIGHT) / (60 * 60)
        patches = [
            (p['tile'], max(1, round(p['count'] * area_scale)), p['radius'])
            for p in cfg.get('patches', [])
        ]
        self.generate_world_map(
            WORLD_WIDTH, WORLD_HEIGHT,
            base_tile=cfg.get('base_tile', 'grass'),
            patches=patches,
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
                # a rocky outcrop: two independent falloff rolls so the deposit
                # has soft edges. an ore cell ALWAYS gets stone beneath it (ore
                # only ever sits on stone, never grass); bare stone fills in
                # around the ore so the patch reads as a rock field with veins
                # rather than ore floating on grass. the ore sprites are ~90%
                # transparent, so the stone shows through prominently.
                p = 1 - (dist / radius)
                ore_here = random.random() < p
                stone_here = random.random() < p
                if ore_here:
                    self.overlay_grid[y][x] = tile_id
                    self.map_grid[y][x] = 'stone'
                elif stone_here:
                    self.map_grid[y][x] = 'stone'

    def in_bounds_tile(self, tx: int, ty: int) -> bool:
        return 0 <= tx < self.width and 0 <= ty < self.height

    def is_walkable(self, tx: int, ty: int) -> bool:
        # in-bounds AND not blocked by a solid entity. all overlay tiles are
        # walkable — only entities with prototype.solid=True block movement.
        if not self.in_bounds_tile(tx, ty):
            return False
        eid = self.tile_index.get((tx, ty))
        if eid is None:
            return True
        entity = self.entities.get(eid)
        return entity is None or not entity.prototype.solid

    def nearest_walkable(self, tx: int, ty: int, max_radius: int = 12) -> tuple[int, int] | None:
        # ring-search outward for the closest walkable tile. used when the
        # click hits a solid (e.g. somewhere on the factory) — we route the
        # player to an adjacent walkable cell instead.
        if self.is_walkable(tx, ty):
            return (tx, ty)
        for r in range(1, max_radius + 1):
            best = None
            best_dist_sq = None
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue  # only the ring
                    candidate = (tx + dx, ty + dy)
                    if not self.is_walkable(*candidate):
                        continue
                    dsq = dx * dx + dy * dy
                    if best is None or dsq < best_dist_sq:
                        best = candidate
                        best_dist_sq = dsq
            if best is not None:
                return best
        return None

    def overlay_at(self, tx: int, ty: int) -> str | None:
        # return the overlay sprite_id at the given tile, or None if the tile
        # is empty or out of bounds. bundles the bounds check that three
        # break-path callers were repeating.
        if not self.in_bounds_tile(tx, ty):
            return None
        return self.overlay_grid[ty][tx]

    def tile_in_reach(self, tx: int, ty: int, max_dist: int = PLAYER_REACH_TILES) -> bool:
        # single-player convenience: reach measured from the fixed 'player'
        # entity's visual center. multi-player callers use the module-level
        # in_reach(player, ...) with their own player, since 'player' doesn't
        # exist server-side (players are player_N) or client-side (local_id).
        return in_reach(self.get_player(), tx, ty, max_dist)

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
        # the local/controlling player. single-player today; on the shared
        # server this becomes per-connection — callers that mean "any player"
        # should use players() instead of assuming a single one.
        return self.entities['player']

    def players(self) -> list:
        # all player entities (carry the 'player' component). server-side this
        # is the player registry; client-side it's just the local player.
        return [e for e in self.entities.values() if 'player' in e.components]

    def entities_with(self, component: str):
        # iterate entities carrying the named component. avoids the
        # full-world for-loop + None-check that every system used to do.
        for ent in self.entities.values():
            if component in ent.components:
                yield ent

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
