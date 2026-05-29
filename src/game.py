
# main game class. orchestrates world update, render order, input.
#
# render order each frame (delegated to renderer.flush at the end):
#   terrain -> overlay -> shadow -> dropped -> entity -> player -> highlight
#   then on top (screen-space, drawn directly): hud -> minimap -> inventory -> held item
#
# the break / drag-mining state machine lives in BreakSystem (breaking.py).
# Game keeps the render queueing helpers since they reach into many
# subsystems' state.

import math

import pygame as pg


def _build_tile_tint(size: int, color: tuple[int, int, int, int]) -> pg.Surface:
    # flat semi-transparent square. low alpha gives a soft shadow look
    # without an outline or gradient.
    surf = pg.Surface((size, size), pg.SRCALPHA)
    surf.fill(color)
    return surf

from config import TILE_LENGTH, TITLE, DROPPED_ITEM_SIZE
from world import World, world_to_tile
from render import Screen, LAYERS, Minimap
from inventory import Inventory
from hud import Hud
from item import load_item, format_quantity, get_item_icon
from settings import load_settings, save_settings
from breaking import BreakSystem
import input_handler


class Game:
    def __init__(self):
        pg.init()
        pg.font.init()
        pg.display.set_caption(TITLE)

        self.settings = load_settings()
        self.screen = Screen(
            self.settings['screen_width'],
            self.settings['screen_height'],
            fullscreen=self.settings['fullscreen'],
        )

        self.world = World()
        self.inventory = Inventory()
        self.hud = Hud()
        self.minimap = Minimap(self.world)
        self.hud.visible = self.settings.get('show_hud', True)
        # break state, drag-mining mode, particle effects all live here.
        self.break_system = BreakSystem(self.world, self.screen.camera, self.minimap)

        # ui state
        self.held_item: dict | None = None
        self.hover_pos: tuple[int, int] = (0, 0)
        # selected_tile is the world tile coord under the cursor each frame,
        # used to draw a highlight overlay.
        self.selected_tile: tuple[int, int] | None = None

        # frame loop state
        self.clock = pg.time.Clock()
        self.dt = 0.0
        self.running = False

        # pre-compute the hover-highlight tints (one per reach state).
        # flat low-alpha square reads as a soft shadow on the tile.
        self._highlight_reach = _build_tile_tint(TILE_LENGTH, (0, 0, 0, 35))
        self._highlight_unreach = _build_tile_tint(TILE_LENGTH, (170, 60, 60, 50))

        self._seed_world()
        self._position_inventory()

    # --- setup helpers ---

    def _seed_world(self) -> None:
        # ore patches live in world.overlay_grid (placed by generate_world_map).
        # a couple of dropped items so pickup is visible right away.
        # factory: 12x8 tile footprint, sprite matches (no overflow). anchored
        # at tile (10, 6) — well inland from every edge, visible to the east
        # of the player spawn (tile 6,6).
        from entity import Entity
        from prototype import load_prototype
        try:
            self.world.add_entity(Entity(load_prototype('factory'), (10 * TILE_LENGTH, 6 * TILE_LENGTH)))
        except ValueError:
            pass
        self.world.spawn_dropped_item('coin', 7, (8 * TILE_LENGTH, 6 * TILE_LENGTH))
        self.world.spawn_dropped_item('copper', 42, (4 * TILE_LENGTH, 6 * TILE_LENGTH))

    def _player_collides_with_solid(self) -> bool:
        # is the player's hitbox overlapping any solid entity's footprint?
        # cheap n² scan over self.world.entities — fine until we have many
        # solids on screen; switch to a spatial index then.
        hb = self.world.get_player().hitbox_rect()
        for entity in self.world.entities.values():
            if entity.prototype.solid and hb.colliderect(entity.collision_rect()):
                return True
        return False

    def _clamp_player_to_bounds(self) -> None:
        # keep the player's *hitbox* inside the map rectangle. clamping the
        # full 128x128 sprite frame would stop the player ~40px before the
        # visible body actually reaches the edge, since most of the frame is
        # transparent padding. hitbox math matches Entity.hitbox_rect().
        player = self.world.get_player()
        sprite_w, sprite_h = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        hitbox_w, hitbox_h = player.prototype.hitbox or (sprite_w, sprite_h)
        hx_off = (sprite_w - hitbox_w) / 2
        hy_off = sprite_h - hitbox_h
        map_w = self.world.width * TILE_LENGTH
        map_h = self.world.height * TILE_LENGTH
        player.world_x = max(-hx_off, min(player.world_x, map_w - hx_off - hitbox_w))
        player.world_y = max(-hy_off, min(player.world_y, map_h - hy_off - hitbox_h))

    def _position_inventory(self) -> None:
        # bottom-left corner with a small margin. (top-left would collide
        # with the HUD overlay.) keep rect in sync with origin so
        # collidepoint checks (used to suppress world clicks/highlight
        # behind the panel) work without waiting for the next render pass.
        panel_h = self.inventory.panel_image.get_height()
        x = 16
        y = self.screen.height - panel_h - 16
        self.inventory.origin = (x, y)
        self.inventory.rect.topleft = (x, y)

    # --- display lifecycle ---

    def toggle_fullscreen(self) -> None:
        self.settings['fullscreen'] = not self.settings['fullscreen']
        save_settings(self.settings)
        self.screen.resize(
            self.settings['screen_width'],
            self.settings['screen_height'],
            fullscreen=self.settings['fullscreen'],
        )
        self._position_inventory()

    # --- main loop ---

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        while self.running:
            self.dt = self.clock.tick(self.settings['fps_cap']) / 1000.0

            input_handler.event_loop(self)
            if not self.running:
                break

            self._update()
            self._render()
            pg.display.flip()

        save_settings({**self.settings, 'show_hud': self.hud.visible})
        pg.quit()

    def stop(self) -> None:
        self.running = False

    # --- per-frame update ---

    def _update(self) -> None:
        player = self.world.get_player()
        dx, dy = input_handler.poll_movement(player, self.dt)
        if dx or dy:
            # apply each axis separately so the player slides along solid
            # walls instead of sticking. each axis reverts on collision.
            if dx:
                player.world_x += dx
                if self._player_collides_with_solid():
                    player.world_x -= dx
            if dy:
                player.world_y += dy
                if self._player_collides_with_solid():
                    player.world_y -= dy
            self._clamp_player_to_bounds()

        # camera follows the player, accounting for sprite size so the player
        # appears centered (not anchored at top-left).
        sprite_size = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        self.screen.camera.follow((player.world_x, player.world_y), sprite_size=sprite_size)

        # auto-pickup drops whose rect overlaps the player's hitbox rect.
        picked = self.world.collect_dropped_in_rect(player.hitbox_rect())
        for drop in picked:
            leftover = self.inventory.add_item(drop.item_id, drop.quantity)
            if leftover > 0:
                # inventory full: spit the leftover back onto the ground
                self.world.spawn_dropped_item(drop.item_id, leftover, drop.world_pos)

        # selection highlight tracks the tile under the cursor (in reach or not)
        wx, wy = self.screen.camera.screen_to_world(self.hover_pos)
        self.selected_tile = world_to_tile((wx, wy))

        # break/drag-mining state machine + particle physics
        self.break_system.tick(self.dt)

    # --- render ---

    def _render(self) -> None:
        self.screen.clear()
        cam = self.screen.camera
        culling = self.screen.culling

        self._queue_terrain(cam, culling)
        self._queue_overlay(cam, culling)
        self._queue_entities(cam, culling)
        self._queue_dropped(cam, culling)
        self._queue_highlight(cam)
        self.break_system.queue_progress_bar(self.screen.renderer, cam)
        self.break_system.queue_particles(self.screen.renderer, cam, culling)

        self.screen.renderer.flush(LAYERS)

        # screen-space ui drawn directly on the display surface, on top of everything
        self._draw_hud()
        self._draw_minimap()
        self.inventory.render(self.screen.surface)
        self._draw_held_item()

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

    def _queue_highlight(self, cam) -> None:
        if self.selected_tile is None:
            return
        # don't show the world-tile highlight when the cursor is over the
        # open inventory panel — the user is interacting with ui, not world.
        if self.inventory.open and self.inventory.rect.collidepoint(self.hover_pos):
            return
        tx, ty = self.selected_tile
        if not self.world.in_bounds_tile(tx, ty):
            return
        sx, sy = cam.world_to_screen((tx * TILE_LENGTH, ty * TILE_LENGTH))
        # soft radial shadow centered on the tile. no outline — the falloff
        # itself draws the eye to the hovered cell. reach validity swaps the
        # pre-built color variant.
        in_reach = self.world.tile_in_reach(tx, ty)
        overlay = self._highlight_reach if in_reach else self._highlight_unreach
        self.screen.renderer.queue('highlight', overlay, (sx, sy))

    # --- hud / held item drawn directly on display (screen-space) ---

    def _draw_hud(self) -> None:
        # clock.get_fps() is smoothed over the last 10 frames
        self.hud.render(
            self.screen.surface,
            fps=self.clock.get_fps(),
            frame_ms=self.dt * 1000,
            n_entities=len(self.world.entities),
            n_dropped=len(self.world.dropped),
        )

    def _draw_minimap(self) -> None:
        player = self.world.get_player()
        # use the player's visual center, not the sprite top-left, so the
        # marker tracks where the character actually is.
        sw, sh = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        center = (player.world_x + sw / 2, player.world_y + sh / 2)
        self.minimap.render(
            self.screen.surface,
            (self.screen.width, self.screen.height),
            self.screen.camera.offset,
            center,
        )

    def _draw_held_item(self) -> None:
        if self.held_item is None:
            return
        proto = load_item(self.held_item['item_id'])
        img = get_item_icon(proto)
        pos = self.held_item['screen_pos']
        self.screen.surface.blit(img, pos)
        if self.held_item['quantity'] > 1:
            label = self.inventory.font.render(
                format_quantity(self.held_item['quantity']), True, (255, 255, 255),
            )
            label_rect = label.get_rect(bottomright=(pos[0] + img.get_width(), pos[1] + img.get_height()))
            self.screen.surface.blit(label, label_rect)
