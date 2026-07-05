
# project paths
DATA_DIR = 'src/data'
ENTITIES_DIR = f'{DATA_DIR}/entities'
ITEMS_DIR = f'{DATA_DIR}/items'
RECIPES_DIR = f'{DATA_DIR}/recipes'

# sprite registry. references both an atlas (tile sheet sliced by row/col)
# and any standalone png files. atlas image path lives inside the json
# itself, so config doesn't need a separate constant for the sheet file.
SPRITES_FILE = f'{DATA_DIR}/sprites.json'

# animation strips: semantic anim_id -> {file, frames, size, margin, spacing, fps}
ANIMATIONS_FILE = f'{DATA_DIR}/animations.json'

# inventory ui asset
INVENTORY_UI_FILE = f'{DATA_DIR}/sprites/ui/inventory.png'

# runtime settings json (persisted across launches)
SETTINGS_FILE = f'{DATA_DIR}/settings.json'

# convention dirs / files for auto-loaded, data-driven content
TILES_DIR = f'{DATA_DIR}/sprites/tiles'          # auto-scanned terrain/ore tiles
ITEM_SPRITES_DIR = f'{DATA_DIR}/sprites/items'   # default item icon location
WORLDGEN_FILE = f'{DATA_DIR}/worldgen.json'      # terrain patches + placements

# display defaults (overridden by settings.json if present)
FPS = 120
SCREEN_WIDTH, SCREEN_HEIGHT = 1280, 720
TITLE = 'rare-earth'

# tile geometry
TILE_LENGTH = 64

# world map dimensions, in tiles. rendering is viewport-culled, so per-frame
# draw cost is independent of these. world generation, the minimap pre-render,
# and save/load all scale with width*height — crank these to stress-test
# loading. (baseline was 60x60.)
WORLD_WIDTH = 1000
WORLD_HEIGHT = 1000

# player
PLAYER_SPAWN = (400, 400)
PLAYER_REACH_TILES = 4
# melee reach (px, center-to-center) for attacking a clicked mob.
PLAYER_ATTACK_RANGE = 120

# world drops
ITEM_STACK_DISTANCE = 40
DROPPED_ITEM_SIZE = 48

# item icon size — every item image (world drops, inventory slots, held
# cursor stack) is scaled to this on load, so a 1254x1254 source PNG and a
# 32x32 source PNG render the same in-game. nearest-neighbor scaling, so
# very large sources (e.g. 1254x1254 coal.png) lose chunk detail at small
# values; bump this up if items look muddy or too small.
ITEM_ICON_SIZE = 48

# inventory layout
INVENTORY_COLS = 10
INVENTORY_ROWS = 4
INVENTORY_SLOTS = INVENTORY_COLS * INVENTORY_ROWS
INVENTORY_SLOT_PX = 34
INVENTORY_BORDER_PX = 4
# item icons in the inventory render at this size — the slot is 34 px with
# a 2-px border between slots, leaving 32 px of visual icon area.
INVENTORY_ICON_SIZE = INVENTORY_SLOT_PX - 2

# exchange drop box: 6x5 grid a player deposits into to fulfil their accepted
# forward contracts. per-player (each player owns their own box + board).
EXCHANGE_DROP_BOX_SLOTS = 30

# culling margin (extra px around viewport so things don't pop at the edge)
CULLING_MARGIN = 96

# day clock: real seconds per in-game day. short value (120) for dev so
# rollover triggers are testable without waiting hours.
DAY_LENGTH_SEC = 120
