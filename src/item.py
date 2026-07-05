
# item prototypes (json) + dropped-item record in the world.
#
# items live in two places:
#   - inside the player's inventory as (item_id, quantity) entries
#   - dropped on the ground as DroppedItem(world_pos, item_id, quantity)
#
# the prototype is just data; rendering is done by the renderer using the
# image referenced from `image_path`.

from dataclasses import dataclass, replace
import json
import random

import pygame as pg

from config import ITEMS_DIR, ITEM_ICON_SIZE, ITEM_SPRITES_DIR, RECIPES_DIR
from dataload import from_dict
from resources import load_image


@dataclass(frozen=True)
class ItemPrototype:
    id: str
    name: str
    # icon image path. json key is "image"; when omitted it defaults by
    # convention to <ITEM_SPRITES_DIR>/<id>.png (see load_item).
    image_path: str | None = None
    # optional per-item icon size override. when None, uses ITEM_ICON_SIZE.
    # lets a single bulky-looking item (e.g. coal) sit slightly larger than
    # finer ones (coin, copper) without changing the default for everything.
    icon_size: int | None = None
    # spot-market eligibility. None = not tradeable on the exchange.
    # when set, this is both the initial price and the mean-reversion
    # target the random walk drifts toward.
    spot_price: int | None = None
    # placement: the entity prototype id this item spawns when placed in the
    # world (build mode, right-hand). None = not placeable. one unit is
    # consumed per placement.
    places: str | None = None


_cache: dict[str, ItemPrototype] = {}
_icon_cache: dict[str, pg.Surface] = {}


def get_item_icon(proto: 'ItemPrototype', size: int | None = None) -> pg.Surface:
    # resolve the rendered icon size in priority order:
    #   1. explicit `size` argument (used by the inventory to force its slot size)
    #   2. proto.icon_size override (per-item tweak in items/*.json)
    #   3. ITEM_ICON_SIZE default
    # cache key includes the resolved size so different size requests for the
    # same source image don't collide.
    if size is None:
        size = proto.icon_size or ITEM_ICON_SIZE
    key = f'{proto.image_path}@{size}'
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    img = load_image(proto.image_path)
    scaled = pg.transform.scale(img, (size, size))
    _icon_cache[key] = scaled
    return scaled


def load_item(item_id: str) -> ItemPrototype:
    if item_id in _cache:
        return _cache[item_id]
    with open(f'{ITEMS_DIR}/{item_id}.json') as f:
        raw = json.load(f)
    raw.setdefault('id', item_id)   # id defaults to the filename stem
    proto = from_dict(ItemPrototype, raw, aliases={'image': 'image_path'})
    if proto.image_path is None:
        # convention: <ITEM_SPRITES_DIR>/<id>.png when "image" is omitted.
        proto = replace(proto, image_path=f'{ITEM_SPRITES_DIR}/{proto.id}.png')
    _cache[item_id] = proto
    return proto


@dataclass
class DroppedItem:
    item_id: str
    quantity: int
    world_x: float
    world_y: float

    @property
    def world_pos(self) -> tuple[float, float]:
        return (self.world_x, self.world_y)


def roll_drops(drops_spec) -> list[tuple[str, int]]:
    # resolve a prototype's drops field into concrete (item_id, qty) pairs.
    # `quantity` per entry can be a plain int (fixed amount) or a [min, max]
    # list/tuple (rolled per break via random.randint, inclusive on both ends).
    # an optional `chance` (0..1, default 1.0) makes the entry a rare drop:
    # it's rolled independently and skipped when it doesn't fire. entries that
    # resolve to 0 (or a missed chance) are dropped so we never spawn an empty
    # stack in the world.
    out: list[tuple[str, int]] = []
    for d in drops_spec:
        if random.random() >= d.get('chance', 1.0):
            continue
        qty = d['quantity']
        if isinstance(qty, (list, tuple)):
            qty = random.randint(qty[0], qty[1])
        if qty <= 0:
            continue
        out.append((d['item'], qty))
    return out


@dataclass(frozen=True)
class Recipe:
    # frozen because recipes are looked up by id; the inputs/outputs lists
    # are stored as tuples of (item_id, quantity) for hashability + immutability.
    id: str
    inputs: tuple[tuple[str, int], ...]
    outputs: tuple[tuple[str, int], ...]
    duration_ms: int


_recipe_cache: dict[str, Recipe] = {}


def load_recipe(recipe_id: str) -> Recipe:
    if recipe_id in _recipe_cache:
        return _recipe_cache[recipe_id]
    with open(f'{RECIPES_DIR}/{recipe_id}.json') as f:
        raw = json.load(f)
    rec = Recipe(
        id=raw['id'],
        inputs=tuple((d['item'], d['quantity']) for d in raw['inputs']),
        outputs=tuple((d['item'], d['quantity']) for d in raw['outputs']),
        duration_ms=int(raw['duration'] * 1000),
    )
    _recipe_cache[recipe_id] = rec
    return rec


def format_quantity(n: int) -> str:
    # short-form display: 1500 -> 1k, 1500000 -> 1.5m. only used for ui labels.
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f'{n // 1000}k'
    return f'{n / 1_000_000:.1f}m'
