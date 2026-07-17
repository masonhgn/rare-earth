
# rendering layer: display, camera, view-frustum culling, batched blitting.
#
# four small classes that collectively answer "how does the world get
# drawn?". kept in one file because they're tightly coupled (Screen owns
# all the others) and the file boundary across four 30-line modules was
# more ceremony than separation.
#
#   Camera        — world<->screen transform + follow target
#   ViewFrustum   — visibility queries (tile range, point check)
#   Renderer      — collects sprite blits into named layers, flushes
#                   them in a fixed order so layering lives in one place
#   Screen        — owns the pg display + the sprite atlas / animation
#                   library + the three above. single facade for the game.
#
# render order is `LAYERS` below; the game-loop calls Screen.renderer.flush(LAYERS)
# once per frame after queueing.

import math
from collections import defaultdict

import numpy as np
import pygame as pg

from config import (
    SPRITES_FILE, ANIMATIONS_FILE, TILE_LENGTH, CULLING_MARGIN,
    SCREEN_WIDTH, SCREEN_HEIGHT, DROPPED_ITEM_SIZE,
)
from item import load_item, get_item_icon
from spritesheet import load_sprites
from animation import AnimationLibrary
from ui_theme import get_font


# layer order used by Renderer.flush each frame. 'rock' (organic rock-patch
# surfaces) sits over the grass base terrain; 'overlay' (ore) draws on top of
# the rock, and both sit under everything dynamic.
LAYERS = ['terrain', 'rock', 'overlay', 'shadow', 'dropped', 'entity', 'player', 'highlight']

# how long the yellow click marker stays visible (fades to 0 over this span)
CLICK_MARKER_MS = 500

# scroll-wheel zoom bounds + per-notch factor. zoom is a scale applied only
# in the final present step (Screen renders the world 1:1 to an offscreen
# surface, then scales it to the display), so >1 magnifies (zoom in) and <1
# shows more world (zoom out). kept modest so pixel art stays legible.
MIN_ZOOM = 0.5
MAX_ZOOM = 2.5
ZOOM_STEP = 1.1


class Camera:
    # offset is the world-space position that maps to screen (0, 0).
    # follow recenters so the target sits at screen midpoint.
    #
    # screen_w/h are the *effective* viewport in world pixels (the offscreen
    # world surface's size), which equals the display size divided by zoom.
    # world_to_screen stays 1:1 because the world is drawn to that offscreen
    # surface before zoom is applied; only screen_to_world (which takes real
    # display/mouse coords) has to divide out the zoom.
    def __init__(self, screen_size: tuple[int, int] = (SCREEN_WIDTH, SCREEN_HEIGHT)):
        self.offset = pg.math.Vector2(0, 0)
        self.screen_w, self.screen_h = screen_size
        self.zoom = 1.0

    def update_screen_size(self, w: int, h: int) -> None:
        self.screen_w, self.screen_h = w, h

    def follow(self, target_world_pos: tuple[float, float], sprite_size: tuple[int, int] | None = None) -> None:
        # sprite_size lets us center on the visual middle of an entity
        # whose anchor is its top-left (e.g. a 128x128 animation frame).
        tw, th = sprite_size or (0, 0)
        cx = target_world_pos[0] + tw / 2
        cy = target_world_pos[1] + th / 2
        self.offset.x = cx - self.screen_w / 2
        self.offset.y = cy - self.screen_h / 2

    def world_to_screen(self, world_pos: tuple[float, float]) -> tuple[float, float]:
        return (world_pos[0] - self.offset.x, world_pos[1] - self.offset.y)

    def screen_to_world(self, screen_pos: tuple[float, float]) -> tuple[float, float]:
        # screen_pos is a real display pixel (e.g. the mouse). undo the zoom
        # scale to land back in the offscreen world surface, then add offset.
        return (screen_pos[0] / self.zoom + self.offset.x,
                screen_pos[1] / self.zoom + self.offset.y)


class ViewFrustum:
    def __init__(self, screen_w: int, screen_h: int, margin: int = CULLING_MARGIN):
        self.margin = margin
        self.screen_w = screen_w
        self.screen_h = screen_h

    def update_screen_size(self, w: int, h: int) -> None:
        self.screen_w, self.screen_h = w, h

    def visible_world_rect(self, camera_offset: pg.math.Vector2) -> pg.Rect:
        return pg.Rect(
            camera_offset.x - self.margin,
            camera_offset.y - self.margin,
            self.screen_w + 2 * self.margin,
            self.screen_h + 2 * self.margin,
        )

    def tile_range(self, camera_offset: pg.math.Vector2, map_w: int, map_h: int):
        # (tx0, ty0, tx1, ty1) for visible tile indices, clamped to the map.
        vr = self.visible_world_rect(camera_offset)
        tx0 = max(int(vr.left // TILE_LENGTH), 0)
        tx1 = min(int(vr.right // TILE_LENGTH) + 1, map_w)
        ty0 = max(int(vr.top // TILE_LENGTH), 0)
        ty1 = min(int(vr.bottom // TILE_LENGTH) + 1, map_h)
        return tx0, ty0, tx1, ty1

    def point_visible(self, world_pos: tuple[float, float], camera_offset: pg.math.Vector2, size: tuple[int, int] = (TILE_LENGTH, TILE_LENGTH)) -> bool:
        vr = self.visible_world_rect(camera_offset)
        x, y = world_pos
        w, h = size
        return vr.colliderect(pg.Rect(x, y, w, h))


class Renderer:
    # per-frame queue of (image, screen_pos) pairs, bucketed by layer name.
    # flush() drains them in a fixed order so layering is centralized here
    # instead of scattered through the game loop.
    def __init__(self, surface: pg.Surface):
        self.surface = surface
        self._batches: dict[str, list[tuple[pg.Surface, tuple[float, float]]]] = defaultdict(list)

    def set_surface(self, surface: pg.Surface) -> None:
        self.surface = surface

    def queue(self, layer: str, image: pg.Surface, screen_pos: tuple[float, float]) -> None:
        self._batches[layer].append((image, screen_pos))

    def flush(self, layer_order: list[str]) -> None:
        for layer in layer_order:
            batch = self._batches.get(layer)
            if batch:
                self.surface.blits(batch)
        self._batches.clear()


def _desktop_size(*, default: tuple[int, int]) -> tuple[int, int]:
    # current desktop resolution of the primary monitor. used for
    # borderless mode to size the surface to the full screen. several
    # pygame builds expose this differently, so we try the canonical
    # call and fall back if it isn't available.
    try:
        sizes = pg.display.get_desktop_sizes()
        if sizes:
            return sizes[0]
    except (AttributeError, pg.error):
        pass
    info = pg.display.Info()
    if info.current_w > 0 and info.current_h > 0:
        return (info.current_w, info.current_h)
    return default


class Screen:
    # display + sprite atlas + animation library + camera + culling + renderer.
    # the single facade the game uses to draw anything.
    #
    # display_mode controls window flavor:
    #   'windowed'   -> standard window at the requested size
    #   'fullscreen' -> exclusive fullscreen at the requested size
    #   'borderless' -> NOFRAME at the desktop's current resolution
    def __init__(self, width: int, height: int, display_mode: str = 'windowed'):
        self._open_surface(width, height, display_mode)

        self.sprites = load_sprites(SPRITES_FILE)

        self.animations = AnimationLibrary(ANIMATIONS_FILE)
        self.animations.load()

        # scroll-wheel zoom. the world is drawn 1:1 into world_surface (sized
        # to the effective viewport = display / zoom), then scaled onto the
        # display in present_world(). the camera/culling/renderer all work in
        # that offscreen space, so only the final present carries the zoom.
        self.zoom = 1.0
        self.world_surface = self._make_world_surface()
        ew, eh = self.world_surface.get_size()
        self.camera = Camera((ew, eh))
        self.camera.zoom = self.zoom
        self.culling = ViewFrustum(ew, eh)
        self.renderer = Renderer(self.world_surface)

    def _make_world_surface(self) -> pg.Surface:
        # offscreen render target sized so that scaling it up by `zoom` fills
        # the display. rounded to whole pixels; clamped to >=1 so a tiny window
        # or extreme zoom can't produce a zero-size surface.
        ew = max(1, round(self.width / self.zoom))
        eh = max(1, round(self.height / self.zoom))
        return pg.Surface((ew, eh)).convert()

    def _sync_world_target(self) -> None:
        # rebuild the offscreen world surface for the current size/zoom and
        # repoint the camera, culling frustum, and renderer at its dimensions.
        self.world_surface = self._make_world_surface()
        ew, eh = self.world_surface.get_size()
        self.camera.zoom = self.zoom
        self.camera.update_screen_size(ew, eh)
        self.culling.update_screen_size(ew, eh)
        self.renderer.set_surface(self.world_surface)

    def set_zoom(self, zoom: float) -> None:
        z = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if abs(z - self.zoom) < 1e-6:
            return
        self.zoom = z
        self._sync_world_target()

    def zoom_by(self, notches: float) -> None:
        # scroll-wheel handler: each notch multiplies zoom by ZOOM_STEP
        # (event.y is +1 per notch up / -1 down; multi-notch scrolls compound).
        if notches:
            self.set_zoom(self.zoom * (ZOOM_STEP ** notches))

    def present_world(self) -> None:
        # scale the offscreen world onto the display. this is the single place
        # zoom is applied; UI is drawn on self.surface afterward at native res.
        pg.transform.scale(self.world_surface, (self.width, self.height), self.surface)

    def resize(self, width: int, height: int, display_mode: str = 'windowed') -> None:
        self._open_surface(width, height, display_mode)
        self._sync_world_target()

    def _open_surface(self, width: int, height: int, display_mode: str) -> None:
        if display_mode == 'fullscreen':
            self.surface = pg.display.set_mode((width, height), pg.FULLSCREEN)
        elif display_mode == 'borderless':
            # borderless = NOFRAME at the desktop's native size, so the
            # window covers the screen without alt-tabbing pain. fall
            # back to the requested size if the desktop probe fails.
            dw, dh = _desktop_size(default=(width, height))
            self.surface = pg.display.set_mode((dw, dh), pg.NOFRAME)
        else:
            self.surface = pg.display.set_mode((width, height), 0)
        self.width, self.height = self.surface.get_size()

    def clear(self, color=(170, 210, 240)) -> None:
        # clears the offscreen world surface (present_world then paints the
        # whole display), so the sky color shows through beyond the map edge.
        self.world_surface.fill(color)


# ---------------------------------------------------------------------------
# overview map shared helpers (corner Minimap + full-screen MapView)
# ---------------------------------------------------------------------------
#
# MAP_TERRAIN_COLORS maps sprite_id -> overview pixel color. unknown tiles
# fall back to a neutral grey so nothing renders invisible.

MAP_TERRAIN_COLORS: dict[str, tuple[int, int, int]] = {
    'grass': (74, 122, 47),
    'coal_ore': (45, 45, 50),
    'copper_ore': (185, 100, 45),
    'iron_ore': (200, 195, 188),
    'silver_ore': (205, 212, 222),
    'haldrite_ore': (150, 85, 205),
    'sand': (220, 195, 130),
    'dirt': (135, 95, 65),
    'stone': (140, 140, 150),
    'cobble': (125, 125, 130),
    'water': (60, 110, 180),
    'planks': (180, 130, 80),
    'brick': (175, 95, 70),
    'cinder': (150, 150, 160),
}
_MAP_GREY = (130, 130, 150)


def _color_for(tile_id) -> tuple[int, int, int]:
    return MAP_TERRAIN_COLORS.get(tile_id, _MAP_GREY)


def _overview_rgb(world) -> np.ndarray:
    # a 1px-per-tile RGB image of the whole map, overlay (ore) preferred over
    # the base terrain, as an (H, W, 3) uint8 array. built by masking whole
    # tile-id classes at once in C rather than the old per-cell python loop.
    h, w = world.height, world.width
    base = np.array(world.map_grid, dtype=object)          # (h, w) of str
    over = np.array(world.overlay_grid, dtype=object)       # (h, w) of str | None
    rgb = np.empty((h, w, 3), dtype=np.uint8)
    rgb[...] = _MAP_GREY
    for tid in set(base.flat):                              # ~a dozen distinct ids
        rgb[base == tid] = _color_for(tid)
    for tid in set(over.flat) - {None}:
        rgb[over == tid] = _color_for(tid)
    return rgb


def _downsample_rgb(rgb: np.ndarray, tpp: int) -> np.ndarray:
    # block-average an (H, W, 3) image by tpp so its longest edge fits a budget.
    # crops the ragged remainder (< tpp) off the far edges — negligible at these
    # scales and keeps the reshape exact.
    if tpp <= 1:
        return rgb
    h, w = rgb.shape[0] // tpp, rgb.shape[1] // tpp
    block = rgb[:h * tpp, :w * tpp].reshape(h, tpp, w, tpp, 3)
    return block.mean(axis=(1, 3)).astype(np.uint8)


def _surface_from_rgb(rgb: np.ndarray) -> pg.Surface:
    # pygame.surfarray is (W, H, 3); our arrays are (H, W, 3) — transpose once.
    return pg.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))


class WorldOverview:
    # cached 1px/tile RGB image of the whole map, shared by the corner Minimap
    # (a slice) and the full MapView (a downsample). replacing the old per-cell
    # set_at loops with numpy slicing turns both into C-speed ops. `version`
    # bumps on every tile edit so the MapView cache knows to rebuild.
    def __init__(self, world):
        self.world = world
        self.rgb = _overview_rgb(world)
        self.version = 0

    def update_cell(self, tx: int, ty: int) -> None:
        if 0 <= ty < self.world.height and 0 <= tx < self.world.width:
            o = self.world.overlay_grid[ty][tx]
            self.rgb[ty, tx] = _color_for(o if o is not None else self.world.map_grid[ty][tx])
            self.version += 1

    def rebuild(self) -> None:
        self.rgb = _overview_rgb(self.world)
        self.version += 1


def get_overview(world) -> WorldOverview:
    # lazily build + cache one overview per world (first minimap/map render).
    ov = getattr(world, '_render_overview', None)
    if ov is None:
        ov = WorldOverview(world)
        world._render_overview = ov
    return ov


def build_world_surface(world, max_px: int) -> tuple[pg.Surface, int]:
    # downsampled snapshot of the whole world. tpp (tiles per pixel) scales so
    # the surface's longest edge stays <= max_px, bounding both its size and
    # build cost regardless of map dimensions. returns (surface, tpp).
    tpp = max(1, math.ceil(max(world.width, world.height) / max_px))
    small = _downsample_rgb(_overview_rgb(world), tpp)
    return _surface_from_rgb(small), tpp


class Minimap:
    # corner overview of the area *around the player* (not the whole world —
    # that's MapView / Tab). a VIEW_TILES x VIEW_TILES window centered on the
    # player's tile, rendered 1px/tile into a small surface then scaled up into
    # the BOX_PX corner box. the window surface is cached and rebuilt only when
    # the player crosses into a new tile (or a visible tile changes), so it's
    # cheap per frame and crisp regardless of how big the world is.

    BOX_PX = 200        # on-screen size of the square minimap box
    VIEW_TILES = 64     # tiles across the window, centered on the player
    VOID = (18, 16, 20)  # cells beyond the map edge

    def __init__(self, world, padding: int = 12):
        self.world = world
        self.padding = padding
        self._surf: pg.Surface | None = None
        self._center: tuple[int, int] | None = None  # tile the cache is built for

    def update_cell(self, tx: int, ty: int) -> None:
        # a tile changed (e.g. ore mined): patch the shared overview and drop the
        # window cache so the next render re-slices it.
        ov = getattr(self.world, '_render_overview', None)
        if ov is not None:
            ov.update_cell(tx, ty)
        self._surf = None

    def rebuild(self) -> None:
        ov = getattr(self.world, '_render_overview', None)
        if ov is not None:
            ov.rebuild()
        self._surf = None

    def _build(self, ctx: int, cty: int) -> pg.Surface:
        # VIEW_TILES square, 1px/tile, centered on (ctx, cty); a numpy slice of
        # the shared overview, VOID-padded where the window runs off the map.
        n = self.VIEW_TILES
        half = n // 2
        w, h = self.world.width, self.world.height
        win = np.empty((n, n, 3), dtype=np.uint8)
        win[...] = self.VOID
        y0, x0 = cty - half, ctx - half
        sy0, sx0 = max(0, y0), max(0, x0)
        sy1, sx1 = min(h, y0 + n), min(w, x0 + n)
        if sy1 > sy0 and sx1 > sx0:
            src = get_overview(self.world).rgb[sy0:sy1, sx0:sx1]
            win[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = src
        return _surface_from_rgb(win)

    def render(self, target: pg.Surface, screen_size: tuple[int, int],
               camera_offset: pg.math.Vector2,
               player_world_pos: tuple[float, float]) -> None:
        # window centers on the player's tile (camera_offset is unused — the
        # player is always at the middle of this local view).
        ctx = int(player_world_pos[0] // TILE_LENGTH)
        cty = int(player_world_pos[1] // TILE_LENGTH)
        if self._surf is None or self._center != (ctx, cty):
            self._surf = self._build(ctx, cty)
            self._center = (ctx, cty)

        box = self.BOX_PX
        x0 = screen_size[0] - box - self.padding
        y0 = self.padding

        backdrop = pg.Surface((box + 4, box + 4), pg.SRCALPHA)
        backdrop.fill((0, 0, 0, 200))
        target.blit(backdrop, (x0 - 2, y0 - 2))
        target.blit(pg.transform.scale(self._surf, (box, box)), (x0, y0))

        # markers. ppt = display px per world tile inside the box.
        n = self.VIEW_TILES
        half = n // 2
        ppt = box / n

        # mobs inside the window (red)
        for mob in self.world.entities_with('mob'):
            dx = mob.world_x / TILE_LENGTH - ctx
            dy = mob.world_y / TILE_LENGTH - cty
            if -half <= dx <= half and -half <= dy <= half:
                mx = x0 + int((dx + half) * ppt)
                my = y0 + int((dy + half) * ppt)
                pg.draw.circle(target, (230, 60, 60), (mx, my), 3)

        # player: always at the box center (the window is centered on them)
        pg.draw.circle(target, (255, 230, 70), (x0 + box // 2, y0 + box // 2), 3)

        # frame
        pg.draw.rect(target, (200, 170, 110), pg.Rect(x0 - 2, y0 - 2, box + 4, box + 4), width=1)


class MapView:
    # full-screen WHOLE-WORLD map overlay, toggled with Tab. unlike the corner
    # Minimap (which is local to the player), this shows the entire world,
    # downsampled. it builds + caches its own surface (rebuilt only if the map
    # dimensions change), drawn centered over a dimmed backdrop with live
    # player/mob markers and the current viewport box.

    MAX_PX = 512

    def __init__(self, world) -> None:
        self.world = world
        self.open = False
        self._surface: pg.Surface | None = None
        self._tpp = 1
        self._version = -1   # overview version this surface was built from

    def toggle(self) -> None:
        self.open = not self.open

    def close(self) -> None:
        self.open = False

    def _ensure_surface(self) -> None:
        # rebuild only when the shared overview changed (map edits / resize),
        # by downsampling it — cheap enough to not need a dims check.
        ov = get_overview(self.world)
        if self._surface is None or self._version != ov.version:
            self._tpp = max(1, math.ceil(max(self.world.width, self.world.height) / self.MAX_PX))
            self._surface = _surface_from_rgb(_downsample_rgb(ov.rgb, self._tpp))
            self._version = ov.version

    def render(self, target: pg.Surface, screen_size: tuple[int, int],
               camera) -> None:
        if not self.open:
            return
        self._ensure_surface()
        world = self.world
        sw, sh = screen_size
        camera_offset = camera.offset

        # dim the world behind the map
        backdrop = pg.Surface((sw, sh), pg.SRCALPHA)
        backdrop.fill((0, 0, 0, 190))
        target.blit(backdrop, (0, 0))

        # fit the whole-world surface within the screen, preserving aspect.
        mw, mh = self._surface.get_size()
        margin = 56
        scale = max(0.01, min((sw - 2 * margin) / mw, (sh - 2 * margin) / mh))
        dw, dh = max(1, int(mw * scale)), max(1, int(mh * scale))
        scaled = pg.transform.scale(self._surface, (dw, dh))
        ox, oy = (sw - dw) // 2, (sh - dh) // 2

        pg.draw.rect(target, (24, 18, 12), pg.Rect(ox - 4, oy - 4, dw + 8, dh + 8))
        target.blit(scaled, (ox, oy))
        pg.draw.rect(target, (200, 170, 110), pg.Rect(ox - 4, oy - 4, dw + 8, dh + 8), width=2)

        # display pixels per world tile = surface-px-per-tile * display scale.
        ppt = (1.0 / self._tpp) * scale

        def to_screen(wx, wy):
            return (ox + int(wx / TILE_LENGTH * ppt), oy + int(wy / TILE_LENGTH * ppt))

        # current viewport rectangle, clipped to the map frame. the visible
        # world area is the camera's effective size (display / zoom), not the
        # display size, so the box stays accurate as the player zooms.
        view = pg.Rect(
            *to_screen(camera_offset.x, camera_offset.y),
            max(1, int((camera.screen_w / TILE_LENGTH) * ppt)),
            max(1, int((camera.screen_h / TILE_LENGTH) * ppt)),
        ).clip(pg.Rect(ox, oy, dw, dh))
        if view.w > 0 and view.h > 0:
            pg.draw.rect(target, (255, 255, 255), view, width=2)

        # mob markers (red), then the player (yellow) on top
        for mob in world.entities_with('mob'):
            mx, my = to_screen(mob.world_x, mob.world_y)
            pg.draw.circle(target, (230, 60, 60), (mx, my), 4)
        player = world.get_player()
        px, py = to_screen(*player.center)
        pg.draw.circle(target, (255, 230, 70), (px, py), 5)

        # title + hint
        target.blit(get_font(28).render('World Map', True, (235, 225, 200)),
                    (ox, max(6, oy - 38)))
        hint = get_font(16).render('Tab / Esc to close', True, (205, 195, 175))
        target.blit(hint, (ox + dw - hint.get_width(), max(6, oy - 26)))


class WorldRenderer:
    # queues the world layers (terrain -> overlay -> entities -> dropped ->
    # click marker) plus the break-system visuals onto the screen renderer,
    # then flushes in LAYERS order. holds only stable refs; per-frame cursor
    # state (the click marker) is passed into flush() and returned back so
    # Game stays the owner of that field.
    def __init__(self, screen, world, break_system):
        self.screen = screen
        self.world = world
        self.break_system = break_system
        # baked rock-patch surfaces, keyed by id(patch) — one composite of the
        # rock texture through the patch's organic mask, blitted each frame. a big
        # world has thousands of patches, so the cache is bounded (see _evict_rock)
        # and keyed by last-visible frame.
        self._rock_cache: dict[int, pg.Surface] = {}
        self._rock_used: dict[int, int] = {}
        self._rock_frame = 0

    def flush(self, cam, culling, click_marker):
        self._queue_terrain(cam, culling)
        self._queue_rock(cam, culling)
        self._queue_overlay(cam, culling)
        self._queue_entities(cam, culling)
        self._queue_dropped(cam, culling)
        click_marker = self._queue_click_marker(cam, click_marker)
        self.break_system.queue_progress_bar(self.screen.renderer, cam)
        self.break_system.queue_particles(self.screen.renderer, cam, culling)
        self.screen.renderer.flush(LAYERS)
        return click_marker

    def _queue_terrain(self, cam, culling) -> None:
        world = self.world
        sprites = self.screen.sprites
        tx0, ty0, tx1, ty1 = culling.tile_range(cam.offset, world.width, world.height)
        grass = sprites.get('grass')
        for ty in range(ty0, ty1):
            row = world.map_grid[ty]
            for tx in range(tx0, tx1):
                tile_id = row[tx]
                # 'stone' is a logical marker now; rock is painted by the rock
                # layer, so the base under it is grass. any other tile draws
                # normally.
                img = grass if tile_id == 'stone' else sprites.get(tile_id)
                if img is None:
                    continue
                sx, sy = cam.world_to_screen((tx * TILE_LENGTH, ty * TILE_LENGTH))
                self.screen.renderer.queue('terrain', img, (sx, sy))

    def _queue_rock(self, cam, culling) -> None:
        # blit each visible rock patch: a rock texture composited through the
        # patch's organic mask (baked once, cached). draws over the grass base
        # and under the ore overlay. patches only exist on freshly generated
        # worlds — loaded/joined worlds have none yet (not saved/streamed).
        patches = getattr(self.world, 'rock_patches', None)
        if not patches:
            return
        self._rock_frame += 1
        vr = culling.visible_world_rect(cam.offset)
        for patch in patches:
            rect = pg.Rect(patch['x'], patch['y'], patch['size'], patch['size'])
            if not vr.colliderect(rect):
                continue
            key = id(patch)
            surf = self._rock_cache.get(key)
            if surf is None:
                surf = self._bake_rock(patch)
                self._rock_cache[key] = surf
            self._rock_used[key] = self._rock_frame
            sx, sy = cam.world_to_screen((patch['x'], patch['y']))
            self.screen.renderer.queue('rock', surf, (sx, sy))
        self._evict_rock()

    def _evict_rock(self, cap: int = 48) -> None:
        # baked patch surfaces are large and there can be thousands of patches, so
        # keep only the most-recently-visible ones. the visible set per frame is
        # small (patches are sparse), so this rarely evicts anything in play.
        if len(self._rock_cache) <= cap:
            return
        stale = sorted(self._rock_cache, key=lambda k: self._rock_used.get(k, 0))
        for key in stale[:len(self._rock_cache) - cap]:
            self._rock_cache.pop(key, None)
            self._rock_used.pop(key, None)

    def _bake_rock(self, patch) -> pg.Surface:
        # regenerate the patch's shape from its seed (not stored on the patch —
        # see World._make_rock_patch), composite the rock texture through it, and
        # cache the result. one-time cost per patch, blitted cheaply thereafter.
        #
        # the alpha SHAPE is baked at quarter resolution and smooth-scaled up: a
        # full-res noise field costs ~135ms (a visible frame hitch when a patch
        # scrolls in), the small one ~10ms, and the upscale antialiases the rock
        # edge for free. the rock TEXTURE stays crisp at full resolution.
        from rockgen import patch_mask
        size = patch['size']
        msize = max(48, size // 4)
        mask = patch_mask(msize, patch['seed'])

        small = pg.Surface((msize, msize))
        px = pg.surfarray.pixels3d(small)
        mt = mask.T                              # (y,x) mask -> (x,y) surfarray
        px[:, :, 0] = mt; px[:, :, 1] = mt; px[:, :, 2] = mt
        del px
        alpha_up = pg.surfarray.array3d(
            pg.transform.smoothscale(small, (size, size)))[:, :, 0]

        tex = self.screen.sprites.get('stone')
        surf = pg.Surface((size, size)).convert_alpha()
        tw, th = tex.get_size()
        for yy in range(0, size, th):
            for xx in range(0, size, tw):
                surf.blit(tex, (xx, yy))
        alpha = pg.surfarray.pixels_alpha(surf)
        alpha[:, :] = alpha_up
        del alpha
        return surf

    def _queue_overlay(self, cam, culling) -> None:
        # sparse layer (most cells are None). ore tiles use alpha so the
        # terrain underneath shows through between chunks. break visuals
        # (jitter + flash) come from break_system, which returns zeros for
        # any tile that isn't the current target.
        world = self.world
        sprites = self.screen.sprites
        tx0, ty0, tx1, ty1 = culling.tile_range(cam.offset, world.width, world.height)
        now_ms = pg.time.get_ticks()

        for ty in range(ty0, ty1):
            row = world.overlay_grid[ty]
            for tx in range(tx0, tx1):
                tile_id = row[tx]
                if tile_id is None:
                    continue
                img = sprites.get(tile_id)
                if img is None:
                    continue
                jx, jy, flash_alpha = self.break_system.visuals_for_overlay_tile((tx, ty), now_ms)
                sx, sy = cam.world_to_screen((tx * TILE_LENGTH, ty * TILE_LENGTH))
                self.screen.renderer.queue('overlay', img, (sx + jx, sy + jy))
                if flash_alpha > 0:
                    self._queue_flash('overlay', img.get_size(), (sx + jx, sy + jy), flash_alpha)

    def _queue_entities(self, cam, culling) -> None:
        now_ms = pg.time.get_ticks()
        sprites = self.screen.sprites
        animations = self.screen.animations

        # everything non-player on the 'entity' layer, all players on the
        # 'player' layer (drawn on top). role-based so it works for a single
        # local player and for many networked players alike — no singleton.
        placed = []
        players = []
        for e in self.world.entities.values():
            (players if e.is_player else placed).append(e)
        placed.sort(key=lambda e: e.world_y)
        for entity in placed:
            self._queue_one_entity(entity, cam, culling, sprites, animations, now_ms, layer='entity')
        players.sort(key=lambda e: e.world_y)
        for entity in players:
            self._queue_one_entity(entity, cam, culling, sprites, animations, now_ms, layer='player')

    def _queue_one_entity(self, entity, cam, culling, sprites, animations, now_ms, *, layer: str) -> None:
        proto = entity.prototype
        ox, oy = proto.render_offset or (0, 0)
        jx, jy, flash_alpha = self.break_system.visuals_for_entity(entity.id, now_ms)

        if entity.anim is not None:
            frame = entity.anim.advance(animations, now_ms)
            size = proto.sprite_size or frame.get_size()
            if not culling.point_visible((entity.world_x + ox, entity.world_y + oy), cam.offset, size=size):
                return
            sx, sy = cam.world_to_screen((entity.world_x + ox, entity.world_y + oy))
            if proto.shadow:
                self._queue_shadow(frame, (sx, sy))
            self.screen.renderer.queue(layer, frame, (sx + jx, sy + jy))
            if flash_alpha > 0:
                self._queue_flash(layer, frame.get_size(), (sx + jx, sy + jy), flash_alpha)
            return

        # composed from sprite cells per row/col in the prototype grid.
        # culling + flash use the actual image size so oversized file sprites
        # (e.g. a 128x128 tree in a 1x1 grid) aren't cut off and their break
        # flash covers the whole sprite, not just a 64x64 corner.
        for r, row in enumerate(entity.render_grid):
            for c, sprite_id in enumerate(row):
                img = sprites.get(sprite_id)
                if img is None:
                    continue
                iw, ih = img.get_size()
                wx = entity.world_x + c * TILE_LENGTH + ox
                wy = entity.world_y + r * TILE_LENGTH + oy
                if not culling.point_visible((wx, wy), cam.offset, size=(iw, ih)):
                    continue
                sx, sy = cam.world_to_screen((wx, wy))
                self.screen.renderer.queue(layer, img, (sx + jx, sy + jy))
                if flash_alpha > 0:
                    self._queue_flash(layer, (iw, ih), (sx + jx, sy + jy), flash_alpha)

    def _queue_flash(self, layer: str, size: tuple[int, int], screen_pos: tuple[float, float], alpha: int) -> None:
        # semi-transparent white blit on top of the sprite. acts like an
        # additive flash because the underlying sprite shows through where
        # alpha < 255.
        overlay = pg.Surface(size, pg.SRCALPHA)
        overlay.fill((255, 255, 255, alpha))
        self.screen.renderer.queue(layer, overlay, screen_pos)

    def _queue_shadow(self, image, screen_pos: tuple[float, float]) -> None:
        # chud-style: a darkened, scaled-down silhouette of the current frame
        # blitted just below the entity. BLEND_RGBA_MULT preserves the sprite's
        # alpha shape (so the shadow follows the character outline, not a
        # rectangle) while crushing rgb to black.
        sw, sh = image.get_size()
        shadow_w, shadow_h = int(sw * 0.78), int(sh * 0.45)
        shadow = pg.transform.smoothscale(image, (shadow_w, shadow_h))
        shadow.fill((0, 0, 0, 130), None, pg.BLEND_RGBA_MULT)
        sx, sy = screen_pos
        x_off = (sw - shadow_w) // 2
        y_off = sh - shadow_h - 4
        self.screen.renderer.queue('shadow', shadow, (sx + x_off, sy + y_off))

    def _queue_dropped(self, cam, culling) -> None:
        # bounce wave so loose items feel alive
        now_ms = pg.time.get_ticks()
        amplitude = 4
        for drop in self.world.dropped:
            if not culling.point_visible(drop.world_pos, cam.offset, size=(DROPPED_ITEM_SIZE, DROPPED_ITEM_SIZE)):
                continue
            proto = load_item(drop.item_id)
            img = get_item_icon(proto)
            # crude bounce per item phase-shifted by world pos to desync
            phase = (drop.world_x + drop.world_y) * 0.01
            offset_y = amplitude * math.sin(now_ms / 1000.0 * math.pi + phase)
            sx, sy = cam.world_to_screen(drop.world_pos)
            self.screen.renderer.queue('dropped', img, (sx, sy + offset_y))

    def _queue_click_marker(self, cam, click_marker):
        # yellow X at the click point, fading over CLICK_MARKER_MS. returns
        # the marker unchanged, or None once it has fully faded.
        if click_marker is None:
            return None
        (wx, wy), born_ms = click_marker
        now_ms = pg.time.get_ticks()
        age = now_ms - born_ms
        if age >= CLICK_MARKER_MS:
            return None
        alpha = int(255 * (1 - age / CLICK_MARKER_MS))
        sx, sy = cam.world_to_screen((wx, wy))
        size = 8
        # draw on a small SRCALPHA surface so we can apply alpha
        surf = pg.Surface((size * 2 + 4, size * 2 + 4), pg.SRCALPHA)
        color = (255, 220, 80, alpha)
        pg.draw.line(surf, color, (2, 2), (size * 2 + 2, size * 2 + 2), 2)
        pg.draw.line(surf, color, (size * 2 + 2, 2), (2, size * 2 + 2), 2)
        self.screen.renderer.queue('highlight', surf, (sx - size - 2, sy - size - 2))
        return click_marker
