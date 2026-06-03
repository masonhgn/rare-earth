
# entity prototype: immutable template loaded from a json file.
#
# rare-earth's original two fields (grid, tile_locked, editable, speed) are
# preserved. optional extensions: animation (state machine of strip ids),
# drops (item drops when broken), shadow (soft cosmetic shadow blit).

from dataclasses import dataclass, field
from typing import Optional
import json

from config import ENTITIES_DIR


@dataclass(frozen=True)
class EntityPrototype:
    # filename-stem id used to load this prototype. preserved on the
    # instance so we can serialize the prototype reference by name when
    # saving entities to disk.
    proto_id: str

    # static composition: 2d list of sprite_ids from the atlas. used when
    # the entity has no animation. each cell is one tile_length square.
    grid: tuple[tuple[str, ...], ...]

    # locks the entity to the tile grid (rocks, trees). non-locked entities
    # move continuously in pixels (player, mobs).
    tile_locked: bool

    # whether the player can place/break this entity at runtime.
    editable: bool

    # pixels per second; only set on movable entities.
    speed: Optional[float] = None

    # optional animation spec: {"default_state": "idle", "states": {...}}
    # if set, the entity renders animation frames instead of `grid`.
    animation: Optional[dict] = None

    # what this entity drops when broken: [{"item": id, "quantity": n}, ...]
    drops: tuple = field(default_factory=tuple)

    # whether to draw a soft shadow under this entity each frame.
    shadow: bool = False

    # optional override for rendered sprite size in pixels (w, h). useful
    # for animation frames that are bigger than one tile (e.g. 128x128 player).
    sprite_size: Optional[tuple[int, int]] = None

    # optional (w, h) hitbox inside the sprite frame. positioned by Entity
    # as bottom-center within sprite_size, so it covers the visible body of
    # the character (the rest of a 128x128 frame is empty padding). when
    # absent, the hitbox equals the full sprite frame.
    hitbox: Optional[tuple[int, int]] = None

    # seconds the player must hold left-click on this entity to break it.
    # 0 means instant break (legacy behavior). only meaningful for editable=True.
    break_time: float = 0.0

    # optional (dx, dy) shift in pixels applied to the rendered sprite,
    # relative to the entity's world position. visual only — the tile
    # footprint and hitbox stay anchored at world_x/y. used to bottom-anchor
    # oversized sprites (e.g. a 128x128 tree on a 64x64 tile uses [-32, -64]
    # so the trunk sits in the anchored tile instead of overflowing right/down).
    render_offset: Optional[tuple[int, int]] = None

    # optional (cols, rows) tile footprint, decoupled from the grid. lets a
    # single-sprite entity (grid 1x1 with a 128x128 image) claim multiple
    # tiles in the tile_index. when absent, footprint matches grid dims.
    footprint_size: Optional[tuple[int, int]] = None

    # whether the player collides with this entity. solids block movement
    # via per-axis revert in Game._update.
    solid: bool = False

    # optional machine spec: {"input_slots": int, "output_slots": int,
    # "recipes": [recipe_id, ...]}. when set, Entity initializes a
    # machine_state dict and FactorySystem ticks the entity each frame.
    machine: Optional[dict] = None

    # optional exchange spec: {"drop_box_slots": int}. when set, Entity
    # initializes exchange_state (drop box, contract board, active list).
    exchange: Optional[dict] = None

    # interaction kind. when set, clicking on the entity opens the
    # registered modal panel for that kind ('factory' / 'exchange').
    # leave None for non-interactable entities (rocks, trees, ore).
    interactable: Optional[str] = None


_cache: dict[str, EntityPrototype] = {}


def _freeze_grid(grid):
    return tuple(tuple(row) for row in grid)


def load_prototype(name: str) -> EntityPrototype:
    if name in _cache:
        return _cache[name]
    with open(f'{ENTITIES_DIR}/{name}.json') as f:
        raw = json.load(f)
    raw['proto_id'] = name
    raw['grid'] = _freeze_grid(raw['grid'])
    if 'drops' in raw:
        raw['drops'] = tuple(raw['drops'])
    if 'sprite_size' in raw and raw['sprite_size'] is not None:
        raw['sprite_size'] = tuple(raw['sprite_size'])
    if 'hitbox' in raw and raw['hitbox'] is not None:
        raw['hitbox'] = tuple(raw['hitbox'])
    if 'render_offset' in raw and raw['render_offset'] is not None:
        raw['render_offset'] = tuple(raw['render_offset'])
    if 'footprint_size' in raw and raw['footprint_size'] is not None:
        raw['footprint_size'] = tuple(raw['footprint_size'])
    _cache[name] = EntityPrototype(**raw)
    return _cache[name]
