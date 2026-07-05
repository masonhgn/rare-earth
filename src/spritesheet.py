
# sprite library. one function loads two kinds of sprites from a single
# sprites.json:
#
#   atlas: a tile sheet sliced into named sprites by (row, col), with
#          optional margin and spacing per cell (so chud-style 651x651
#          tilesets with 1px borders work as well as our flush atlases).
#   files: standalone png files mapped to a name. lets you drop a new
#          sprite in data/sprites/ and reference it from an entity prototype
#          without packing it into an atlas first.
#
# both kinds resolve into the same name -> pg.Surface dict, so callers
# (the game's renderer, entity grid lookups) don't care which side a
# sprite came from. file sprites use their image's natural size, which
# can be larger than TILE_LENGTH — useful for trees and props whose
# visual reaches past their tile footprint.
#
# sprites.json shape:
# {
#   "atlas": {
#     "file": "src/data/sprites/tileset.png",
#     "tile_size": 64,
#     "margin": 1,           # optional, defaults to 0
#     "spacing": 1,          # optional, defaults to 0
#     "sprites": { "grass": [0, 0], "sand": [0, 1], ... }
#   },
#   "files": {
#     "tree": "src/data/sprites/tree.png",
#     "copper_ore": {"file": "src/data/sprites/tiles/copper_ore_tile.png", "size": [64, 64]}
#   }
# }

import json
import os

import pygame as pg

from config import TILE_LENGTH, TILES_DIR


def slice_cell(sheet: pg.Surface, x: int, y: int, w: int, h: int) -> pg.Surface:
    # copy a (w x h) region at (x, y) out of `sheet` into its own alpha
    # surface. shared by the atlas loader here and the animation strip
    # slicer so the "cut a cell from a sheet" idiom lives in one place.
    cell = pg.Surface((w, h), pg.SRCALPHA).convert_alpha()
    cell.blit(sheet, (0, 0), (x, y, w, h))
    return cell


def load_sprites(config_file: str) -> dict[str, pg.Surface]:
    with open(config_file) as f:
        config = json.load(f)
    sprites: dict[str, pg.Surface] = {}
    atlas = config.get('atlas')
    if atlas:
        sprites.update(_load_atlas(atlas))
    files = config.get('files')
    if files:
        sprites.update(_load_files(files))
    _autoscan_tiles(sprites)
    return sprites


def _autoscan_tiles(sprites: dict[str, pg.Surface]) -> None:
    # convention: any src/data/sprites/tiles/<id>.png (or <id>_tile.png) is
    # auto-registered as sprite-id <id>, scaled to one tile, UNLESS an explicit
    # atlas/files entry already defines that id (which wins — e.g. oversized
    # building sprites like factory/exchange that live here but need their own
    # size). lets a new terrain/ore tile be added by just dropping a 64x64 png,
    # with no sprites.json edit.
    if not os.path.isdir(TILES_DIR):
        return
    for fn in os.listdir(TILES_DIR):
        if not fn.endswith('.png'):
            continue
        stem = fn[:-4]
        sprite_id = stem[:-5] if stem.endswith('_tile') else stem
        if sprite_id in sprites:
            continue
        surf = pg.image.load(os.path.join(TILES_DIR, fn)).convert_alpha()
        sprites[sprite_id] = _scale_cover(surf, (TILE_LENGTH, TILE_LENGTH))


def _load_atlas(spec: dict) -> dict[str, pg.Surface]:
    sheet = pg.image.load(spec['file']).convert_alpha()
    tile_size = spec['tile_size']
    margin = spec.get('margin', 0)
    spacing = spec.get('spacing', 0)
    step = tile_size + spacing
    out: dict[str, pg.Surface] = {}
    for name, pos in spec['sprites'].items():
        row, col = pos
        x = margin + col * step
        y = margin + row * step
        out[name] = slice_cell(sheet, x, y, tile_size, tile_size)
    return out


def _load_files(spec: dict) -> dict[str, pg.Surface]:
    # entries can be a bare path or an object {"file": path, "size": [w, h]}.
    # when `size` is set, the source is scaled uniformly to cover the target
    # rect (preserving aspect ratio) then center-cropped. avoids the squish
    # that a plain non-uniform scale produces on non-square sources.
    out: dict[str, pg.Surface] = {}
    for name, entry in spec.items():
        if isinstance(entry, str):
            path, size = entry, None
        else:
            path = entry['file']
            size = tuple(entry['size']) if 'size' in entry else None
        surf = pg.image.load(path).convert_alpha()
        if size is not None:
            surf = _scale_cover(surf, size)
        out[name] = surf
    return out


def _scale_cover(surf: pg.Surface, target: tuple[int, int]) -> pg.Surface:
    # uniform "cover" scale: pick the factor that makes the image fill the
    # target on its tighter axis, then center-blit so excess on the looser
    # axis is cropped equally on both sides. nearest-neighbor scale keeps
    # pixel-art edges crisp.
    sw, sh = surf.get_size()
    tw, th = target
    factor = max(tw / sw, th / sh)
    nw, nh = int(round(sw * factor)), int(round(sh * factor))
    scaled = pg.transform.scale(surf, (nw, nh))
    out = pg.Surface(target, pg.SRCALPHA)
    out.blit(scaled, ((tw - nw) // 2, (th - nh) // 2))
    return out
