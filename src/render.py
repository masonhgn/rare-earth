
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

from collections import defaultdict

import pygame as pg

from config import (
    SPRITES_FILE, ANIMATIONS_FILE, TILE_LENGTH, CULLING_MARGIN,
    SCREEN_WIDTH, SCREEN_HEIGHT,
)
from spritesheet import load_sprites
from animation import AnimationLibrary


# layer order used by Renderer.flush each frame. 'overlay' sits between
# terrain and shadow so ore/feature decoration draws on top of the base
# terrain but underneath everything dynamic.
LAYERS = ['terrain', 'overlay', 'shadow', 'dropped', 'entity', 'player', 'highlight']


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


class Screen:
    # display + sprite atlas + animation library + camera + culling + renderer.
    # the single facade the game uses to draw anything.
    def __init__(self, width: int, height: int, fullscreen: bool = False):
        flags = pg.FULLSCREEN if fullscreen else 0
        self.surface = pg.display.set_mode((width, height), flags)
        self.width, self.height = self.surface.get_size()

        self.sprites = load_sprites(SPRITES_FILE)

        self.animations = AnimationLibrary(ANIMATIONS_FILE)
        self.animations.load()

        self.camera = Camera((self.width, self.height))
        self.culling = ViewFrustum(self.width, self.height)
        self.renderer = Renderer(self.surface)

    def resize(self, width: int, height: int, fullscreen: bool = False) -> None:
        flags = pg.FULLSCREEN if fullscreen else 0
        self.surface = pg.display.set_mode((width, height), flags)
        self.width, self.height = self.surface.get_size()
        self.camera.update_screen_size(self.width, self.height)
        self.culling.update_screen_size(self.width, self.height)
        self.renderer.set_surface(self.surface)

    def clear(self, color=(20, 20, 28)) -> None:
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

        # player marker last so it always sits on top
        px = x0 + int(player_world_pos[0] / TILE_LENGTH * cs)
        py = y0 + int(player_world_pos[1] / TILE_LENGTH * cs)
        pg.draw.circle(target, (255, 230, 70), (px, py), max(2, cs))
