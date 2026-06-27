
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

import pygame as pg

from config import (
    SPRITES_FILE, ANIMATIONS_FILE, TILE_LENGTH, CULLING_MARGIN,
    SCREEN_WIDTH, SCREEN_HEIGHT, DROPPED_ITEM_SIZE,
)
from item import load_item, get_item_icon
from spritesheet import load_sprites
from animation import AnimationLibrary


# layer order used by Renderer.flush each frame. 'overlay' sits between
# terrain and shadow so ore/feature decoration draws on top of the base
# terrain but underneath everything dynamic.
LAYERS = ['terrain', 'overlay', 'shadow', 'dropped', 'entity', 'player', 'highlight']

# how long the yellow click marker stays visible (fades to 0 over this span)
CLICK_MARKER_MS = 500


class Camera:
    # offset is the world-space position that maps to screen (0, 0).
    # follow recenters so the target sits at screen midpoint.
    def __init__(self, screen_size: tuple[int, int] = (SCREEN_WIDTH, SCREEN_HEIGHT)):
        self.offset = pg.math.Vector2(0, 0)
        self.screen_w, self.screen_h = screen_size

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
        return (screen_pos[0] + self.offset.x, screen_pos[1] + self.offset.y)


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

        self.camera = Camera((self.width, self.height))
        self.culling = ViewFrustum(self.width, self.height)
        self.renderer = Renderer(self.surface)

    def resize(self, width: int, height: int, display_mode: str = 'windowed') -> None:
        self._open_surface(width, height, display_mode)
        self.camera.update_screen_size(self.width, self.height)
        self.culling.update_screen_size(self.width, self.height)
        self.renderer.set_surface(self.surface)

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
        self.surface.fill(color)


class Minimap:
    # top-down overview rendered in a screen corner. terrain is cached as
    # a static surface (one-time build at world load); only the player
    # marker and view rectangle are redrawn each frame, which is cheap.
    #
    # TERRAIN_COLORS maps sprite_id -> minimap pixel color. unknown tiles
    # fall back to a neutral grey so nothing renders invisible.

    TERRAIN_COLORS: dict[str, tuple[int, int, int]] = {
        'grass': (74, 122, 47),
        'coal_ore': (45, 45, 50),
        'copper_ore': (185, 100, 45),
        'sand': (220, 195, 130),
        'dirt': (135, 95, 65),
        'stone': (140, 140, 150),
        'cobble': (125, 125, 130),
        'water': (60, 110, 180),
        'planks': (180, 130, 80),
        'brick': (175, 95, 70),
        'cinder': (150, 150, 160),
    }

    def __init__(self, world, cell_size: int = 2, padding: int = 12):
        self.world = world
        self.cell_size = cell_size
        self.padding = padding
        self.terrain = self._build_terrain()

    def _build_terrain(self) -> pg.Surface:
        # paint the base terrain layer, then stamp any overlay tile color
        # over the top so ore patches read on the minimap.
        cs = self.cell_size
        w = self.world.width * cs
        h = self.world.height * cs
        surf = pg.Surface((w, h), pg.SRCALPHA)
        overlay = getattr(self.world, 'overlay_grid', None)
        for y, row in enumerate(self.world.map_grid):
            for x, tile in enumerate(row):
                rect = pg.Rect(x * cs, y * cs, cs, cs)
                surf.fill(self.TERRAIN_COLORS.get(tile, (130, 130, 150)), rect)
                if overlay is not None:
                    top = overlay[y][x]
                    if top is not None:
                        surf.fill(self.TERRAIN_COLORS.get(top, (130, 130, 150)), rect)
        return surf

    def rebuild(self) -> None:
        # call after any mutation to world.map_grid that needs to show up.
        self.terrain = self._build_terrain()

    def render(self, target: pg.Surface, screen_size: tuple[int, int],
               camera_offset: pg.math.Vector2,
               player_world_pos: tuple[float, float]) -> None:
        cs = self.cell_size
        tw, th = self.terrain.get_size()
        # top-right corner placement
        x0 = screen_size[0] - tw - self.padding
        y0 = self.padding

        # darker backdrop so the minimap reads against any terrain underneath
        backdrop = pg.Surface((tw + 4, th + 4), pg.SRCALPHA)
        backdrop.fill((0, 0, 0, 200))
        target.blit(backdrop, (x0 - 2, y0 - 2))
        target.blit(self.terrain, (x0, y0))

        # view rect: project the visible world area onto the minimap so the
        # player can see what slice of the world they're currently looking at.
        sw, sh = screen_size
        vw = (sw / TILE_LENGTH) * cs
        vh = (sh / TILE_LENGTH) * cs
        vx = x0 + (camera_offset.x / TILE_LENGTH) * cs
        vy = y0 + (camera_offset.y / TILE_LENGTH) * cs
        view_rect = pg.Rect(int(vx), int(vy), int(vw), int(vh))
        clipped = view_rect.clip(pg.Rect(x0, y0, tw, th))
        if clipped.w > 0 and clipped.h > 0:
            pg.draw.rect(target, (255, 255, 255), clipped, width=1)

        # mob markers (red), drawn before the player so the player stays on
        # top. Minimap already holds a world ref, so this is self-contained.
        for mob in self.world.entities_with('mob'):
            mx = x0 + int(mob.world_x / TILE_LENGTH * cs)
            my = y0 + int(mob.world_y / TILE_LENGTH * cs)
            pg.draw.circle(target, (230, 60, 60), (mx, my), max(2, cs))

        # player marker last so it always sits on top
        px = x0 + int(player_world_pos[0] / TILE_LENGTH * cs)
        py = y0 + int(player_world_pos[1] / TILE_LENGTH * cs)
        pg.draw.circle(target, (255, 230, 70), (px, py), max(2, cs))


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

    def flush(self, cam, culling, click_marker):
        self._queue_terrain(cam, culling)
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
        for ty in range(ty0, ty1):
            row = world.map_grid[ty]
            for tx in range(tx0, tx1):
                img = sprites.get(row[tx])
                if img is None:
                    continue
                sx, sy = cam.world_to_screen((tx * TILE_LENGTH, ty * TILE_LENGTH))
                self.screen.renderer.queue('terrain', img, (sx, sy))

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
        player = self.world.get_player()
        now_ms = pg.time.get_ticks()
        sprites = self.screen.sprites
        animations = self.screen.animations

        # split placed entities (non-player) for crude y-sort, then player last
        placed = [e for e in self.world.entities.values() if e is not player]
        placed.sort(key=lambda e: e.world_y)
        for entity in placed:
            self._queue_one_entity(entity, cam, culling, sprites, animations, now_ms, layer='entity')
        self._queue_one_entity(player, cam, culling, sprites, animations, now_ms, layer='player')

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
        for r, row in enumerate(proto.grid):
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
