
# shared visual constants for modal panels and widgets.
#
# panel-skin paths, inner margins, header sizes, and the color palette
# all live here so a tweak (e.g. swapping the panel art) doesn't require
# touching every modal source file.
#
# colors are named by role (text-primary, text-muted, accent, etc.)
# rather than appearance (cream, brown) so a palette swap is a one-line
# change in this module instead of a project-wide search.

import os

import pygame as pg

_FONT_DIR = os.path.join(os.path.dirname(__file__), 'data', 'fonts')


def get_font(size: int) -> pg.font.Font:
    # single place the ui builds fonts, so swapping the default family for
    # a bundled TTF later is one edit here instead of a per-file search.
    return pg.font.Font(None, size)


# cache keyed by size: Font construction reads the ttf off disk each call,
# and text fields rebuild their font every frame, so memoize.
_mono_cache: dict[int, pg.font.Font] = {}


def get_mono_font(size: int) -> pg.font.Font:
    # bundled monospace (DejaVu Sans Mono, OFL/public-domain) for anything
    # where even glyph spacing matters: server-address fields, the dev
    # console input, and other typed-text widgets.
    font = _mono_cache.get(size)
    if font is None:
        font = pg.font.Font(os.path.join(_FONT_DIR, 'mono.ttf'), size)
        _mono_cache[size] = font
    return font


# 9-slice panel art used by every modal. scale=0.5 in NineSliceSkin gets
# the rails to a non-bulky thickness on a 1280x720 viewport.
PANEL_SKIN_FILE = 'src/data/sprites/ui/ui_tile.png'
PANEL_SKIN_CORNER = 200
PANEL_SKIN_SCALE = 0.5


# common modal layout constants. exchange + settings + future panels
# all share these so they look like the same kind of window.
MODAL_INNER_MARGIN = 72
MODAL_HEADER_H = 36
MODAL_TAB_H = 38
MODAL_TAB_GAP = 10


# --- color palette ---
#
# text colors stratify by emphasis. ui buttons, tab strips, and row
# stripes all pull their fills/borders from here. tweak in one place
# and the whole ui follows.

COLOR_TEXT_PRIMARY = (245, 235, 215)   # titles, important labels
COLOR_TEXT_BODY = (240, 232, 215)      # button labels on dark buttons
COLOR_TEXT_MUTED = (220, 205, 180)     # section captions
COLOR_TEXT_FAINT = (200, 185, 160)     # hints, placeholder strings
COLOR_TEXT_DIM = (160, 150, 130)       # disabled-looking text
COLOR_TEXT_GHOSTED = (140, 130, 110)   # "(taken)" markers

COLOR_ACCENT_GOLD = (250, 220, 130)    # coin / price / highlight numbers
COLOR_ACCENT_GOLD_DIM = (220, 195, 130)  # arrows, secondary gold

COLOR_BUTTON_BG = (110, 85, 55)
COLOR_BUTTON_BG_DISABLED = (55, 45, 35)
COLOR_BUTTON_BORDER = (30, 22, 16)
COLOR_BUTTON_TEXT_DISABLED = (130, 120, 105)

COLOR_TAB_ACTIVE_BG = (135, 105, 70)
COLOR_TAB_ACTIVE_BORDER = (245, 220, 150)
COLOR_TAB_INACTIVE_BG = (70, 55, 40)
COLOR_TAB_INACTIVE_BORDER = (32, 22, 16)
COLOR_TAB_INACTIVE_TEXT = (200, 190, 175)

COLOR_SLOT_BG = (60, 45, 30)
COLOR_SLOT_BORDER = (32, 22, 16)
COLOR_SLOT_QTY_TEXT = (255, 255, 255)  # stack-size badge on slots / cursor

COLOR_SCROLLBAR_TRACK = (50, 40, 30)
COLOR_SCROLLBAR_THUMB = (140, 110, 75)

COLOR_ROW_STRIPE = (0, 0, 0, 30)       # subtle dark tint on alternating rows

# spot-tab price-history sparkline. line color reflects the window trend
# (last point vs first); the backdrop is a solid dark well since the main
# display surface has no per-pixel alpha to blend against.
COLOR_SPARK_UP = (120, 200, 120)
COLOR_SPARK_DOWN = (210, 110, 90)
COLOR_SPARK_BG = (44, 33, 22)


# --- widget corner radii (px) ---
# named by role so the rounded-rect look stays consistent and a restyle
# is a one-line change here.
RADIUS_TAB = 6
RADIUS_BUTTON = 5
RADIUS_SLOT = 4
RADIUS_SCROLLBAR = 3
