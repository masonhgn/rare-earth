
# generate a labeled contact-sheet png so you can see which [row, col]
# position in an atlas contains which tile. run from project root:
#
#     python src/tools/atlas_contact_sheet.py <atlas_png> [tile_size] [margin] [spacing] [out_png]
#
# defaults: tile_size=64, margin=1, spacing=1, out_png=atlas_contact.png.
# each cell is drawn at 2x scale with its [row, col] coordinate stamped
# in the corner — copy the coords directly into sprites.json.

import os
import sys
import pygame as pg


def build_contact_sheet(atlas_path: str, tile_size: int, margin: int, spacing: int) -> pg.Surface:
    pg.init()
    pg.font.init()
    pg.display.set_mode((1, 1), pg.HIDDEN if hasattr(pg, 'HIDDEN') else 0)

    sheet = pg.image.load(atlas_path).convert_alpha()
    sw, sh = sheet.get_size()

    step = tile_size + spacing
    cols = (sw - margin + spacing) // step
    rows = (sh - margin + spacing) // step

    scale = 2
    cell = tile_size * scale
    label_h = 18
    pad = 4
    out_w = cols * (cell + pad) + pad
    out_h = rows * (cell + label_h + pad) + pad
    out = pg.Surface((out_w, out_h), pg.SRCALPHA)
    out.fill((24, 24, 28))

    font = pg.font.Font(None, 16)
    for r in range(rows):
        for c in range(cols):
            x = margin + c * step
            y = margin + r * step
            tile = pg.Surface((tile_size, tile_size), pg.SRCALPHA)
            tile.blit(sheet, (0, 0), (x, y, tile_size, tile_size))
            scaled = pg.transform.scale(tile, (cell, cell))
            ox = pad + c * (cell + pad)
            oy = pad + r * (cell + label_h + pad)
            out.blit(scaled, (ox, oy))
            label = font.render(f'[{r},{c}]', True, (240, 240, 240))
            out.blit(label, (ox, oy + cell + 1))

    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    atlas_path = argv[1]
    tile_size = int(argv[2]) if len(argv) > 2 else 64
    margin = int(argv[3]) if len(argv) > 3 else 1
    spacing = int(argv[4]) if len(argv) > 4 else 1
    out_path = argv[5] if len(argv) > 5 else 'atlas_contact.png'
    if not os.path.exists(atlas_path):
        print(f'no such file: {atlas_path}')
        return 1
    surf = build_contact_sheet(atlas_path, tile_size, margin, spacing)
    pg.image.save(surf, out_path)
    print(f'wrote {out_path}  ({surf.get_width()}x{surf.get_height()})')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
