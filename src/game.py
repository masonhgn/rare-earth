
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

from config import TILE_LENGTH, TITLE, DROPPED_ITEM_SIZE
from world import World, world_to_tile
from render import Screen, LAYERS, Minimap
from inventory import Inventory
from hud import Hud
from item import load_item, format_quantity, get_item_icon
from settings import load_settings, save_settings
from breaking import BreakSystem
from factory import FactorySystem, FactoryPanel
import input_handler


# how long the yellow click marker stays visible (fades to 0 over this span)
CLICK_MARKER_MS = 500


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
        # factory system + modal panel ui for machine entities.
        self.factory_system = FactorySystem(self.world)
        self.factory_panel = FactoryPanel()

        # ui state
        self.held_item: dict | None = None
        self.hover_pos: tuple[int, int] = (0, 0)

        # click marker: yellow X drawn at the click world pos, fading over
        # CLICK_MARKER_MS. None when no recent click.
        self.click_marker: tuple[tuple[float, float], int] | None = None

        # pending break target: set when the player click-walked toward a
        # breakable. populated as (entity_id_or_None, tile, world_pos_center).
        # cleared on WASD preempt or successful break.
        self.pending_break: tuple | None = None
        # pending factory-open target: set when the player click-walked toward
        # a machine entity. fires open_for() the moment any footprint tile is
        # in reach. cleared on WASD preempt.
        self.pending_open = None

        # frame loop state
        self.clock = pg.time.Clock()
        self.dt = 0.0
        self.running = False


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

    def close_factory_panel(self) -> None:
        # closes the panel AND deposits any cursor-held item back into the
        # player inventory. without this, walking away from the factory while
        # holding an item leaves it stuck in held_item — subsequent world
        # clicks then drop the invisible item and never reach the walk logic.
        if self.held_item is not None:
            leftover = self.inventory.add_item(
                self.held_item['item_id'], self.held_item['quantity'],
            )
            if leftover > 0:
                # inventory full — fall back to dropping at the player's feet
                player = self.world.get_player()
                sw, sh = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
                cx = player.world_x + sw / 2
                cy = player.world_y + sh / 2
                self.world.spawn_dropped_item(self.held_item['item_id'], leftover, (cx, cy))
            self.held_item = None
        self.factory_panel.close()

    def _player_collides_with_solid(self) -> bool:
        # is the player's hitbox overlapping any solid entity's footprint?
        # cheap n² scan over self.world.entities — fine until we have many
        # solids on screen; switch to a spatial index then.
        hb = self.world.get_player().hitbox_rect()
        for entity in self.world.entities.values():
            if entity.prototype.solid and hb.colliderect(entity.collision_rect()):
                return True
        return False

    def _follow_path(self, player) -> tuple[float, float]:
        # walk along the path at the player's speed. consumes as much of
        # this frame's step as possible — if the player arrives at a
        # waypoint with leftover step, the loop continues to the next
        # waypoint in the same frame (so animation never sees a 0-vector
        # mid-walk just because a waypoint flipped). returns the total
        # (dx, dy) actually applied, used by _update_player_animation.
        sprite_w, sprite_h = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        speed = player.prototype.speed or 0.0
        step_remaining = speed * self.dt
        total_dx = total_dy = 0.0
        while player.path and step_remaining > 0:
            wp_tx, wp_ty = player.path[0]
            target_x = wp_tx * TILE_LENGTH + TILE_LENGTH / 2
            target_y = wp_ty * TILE_LENGTH + TILE_LENGTH / 2
            center_x = player.world_x + sprite_w / 2
            center_y = player.world_y + sprite_h / 2
            dx = target_x - center_x
            dy = target_y - center_y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 4:
                player.path.pop(0)
                continue
            if step_remaining >= dist:
                # cover the rest of the leg this frame; pop and keep going.
                player.world_x += dx
                player.world_y += dy
                total_dx += dx
                total_dy += dy
                step_remaining -= dist
                player.path.pop(0)
            else:
                # partial step toward the waypoint.
                nx = dx / dist
                ny = dy / dist
                player.world_x += nx * step_remaining
                player.world_y += ny * step_remaining
                total_dx += nx * step_remaining
                total_dy += ny * step_remaining
                step_remaining = 0
        return total_dx, total_dy

    def _update_player_animation(self, player, dx: float, dy: float) -> None:
        # single canonical animation update, run after movement is resolved.
        # only sets idle when nothing actually moved this frame. preserves
        # the existing horizontal facing during pure-vertical movement, so
        # north/south path segments don't flicker the sprite to front-facing.
        if player.anim is None:
            return
        if dx == 0 and dy == 0:
            player.anim.set_state('idle')
            return
        # threshold avoids flipping facing for tiny rounding-noise dx values
        if dx > 0.5:
            player.anim.set_state('walking_right')
        elif dx < -0.5:
            player.anim.set_state('walking_left')
        elif player.anim.current_state not in ('walking_left', 'walking_right'):
            # we ARE moving (pure vertical) but were idle — pick a default
            # facing so the sprite doesn't stay front-facing mid-walk.
            player.anim.set_state('walking_right')

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
        moved_dx = moved_dy = 0.0
        if dx or dy:
            # manual WASD movement preempts any active path.
            player.path = []
            self.pending_break = None
            self.pending_open = None
            # apply each axis separately so the player slides along solid
            # walls instead of sticking. each axis reverts on collision.
            if dx:
                player.world_x += dx
                if self._player_collides_with_solid():
                    player.world_x -= dx
                else:
                    moved_dx = dx
            if dy:
                player.world_y += dy
                if self._player_collides_with_solid():
                    player.world_y -= dy
                else:
                    moved_dy = dy
            self._clamp_player_to_bounds()
        elif player.path:
            moved_dx, moved_dy = self._follow_path(player)

        # one canonical place to update the player's facing/animation state,
        # using the *actual* movement vector applied this frame.
        self._update_player_animation(player, moved_dx, moved_dy)

        # if a click-walk pointed at a breakable, auto-fire the break the
        # moment the player is within reach. clears path + pending so the
        # player stops walking.
        if self.pending_break is not None:
            proto, entity_id, tile = self.pending_break
            if self.world.tile_in_reach(*tile):
                # also make sure the breakable still exists (might have been
                # broken by drag-mining mid-walk, etc).
                still_there = (
                    entity_id is None and self.world.overlay_at(*tile) is not None
                ) or (
                    entity_id is not None and entity_id in self.world.entities
                )
                if still_there:
                    self.break_system.start_break(proto, tile, entity_id=entity_id)
                player.path = []
                self.pending_break = None

        # click-to-open-factory: same pattern as pending_break. fire as soon
        # as the player is adjacent to any footprint tile of the target machine.
        if self.pending_open is not None:
            machine = self.pending_open
            if any(self.world.tile_in_reach(tx, ty, max_dist=1) for tx, ty in machine.footprint()):
                self.factory_panel.open_for(machine, (self.screen.width, self.screen.height))
                self.inventory.open = True
                player.path = []
                self.pending_open = None

        # auto-close: if the panel is open and the player has wandered out
        # of adjacency (manual WASD or path mid-walk), close it and return
        # any held item to inventory so the player isn't stuck holding it.
        if self.factory_panel.open and self.factory_panel.entity is not None:
            machine = self.factory_panel.entity
            if not any(self.world.tile_in_reach(tx, ty, max_dist=1) for tx, ty in machine.footprint()):
                self.close_factory_panel()

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

        # break/drag-mining state machine + particle physics
        self.break_system.tick(self.dt)
        # advance any in-progress machine recipes
        self.factory_system.tick()

    # --- render ---

    def _render(self) -> None:
        self.screen.clear()
        cam = self.screen.camera
        culling = self.screen.culling

        self._queue_terrain(cam, culling)
        self._queue_overlay(cam, culling)
        self._queue_entities(cam, culling)
        self._queue_dropped(cam, culling)
        self._queue_click_marker(cam)
        self.break_system.queue_progress_bar(self.screen.renderer, cam)
        self.break_system.queue_particles(self.screen.renderer, cam, culling)

        self.screen.renderer.flush(LAYERS)

        # screen-space ui drawn directly on the display surface, on top of everything
        self._draw_hud()
        self._draw_minimap()
        self.inventory.render(self.screen.surface)
        self.factory_panel.render(self.screen.surface, (self.screen.width, self.screen.height))
        self._draw_hover_tooltip()
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

    def _queue_click_marker(self, cam) -> None:
        # yellow X at the click point, fading over CLICK_MARKER_MS.
        if self.click_marker is None:
            return
        (wx, wy), born_ms = self.click_marker
        now_ms = pg.time.get_ticks()
        age = now_ms - born_ms
        if age >= CLICK_MARKER_MS:
            self.click_marker = None
            return
        alpha = int(255 * (1 - age / CLICK_MARKER_MS))
        sx, sy = cam.world_to_screen((wx, wy))
        size = 12
        # draw on a small SRCALPHA surface so we can apply alpha
        surf = pg.Surface((size * 2 + 4, size * 2 + 4), pg.SRCALPHA)
        color = (255, 220, 80, alpha)
        pg.draw.line(surf, color, (2, 2), (size * 2 + 2, size * 2 + 2), 3)
        pg.draw.line(surf, color, (size * 2 + 2, 2), (2, size * 2 + 2), 3)
        self.screen.renderer.queue('highlight', surf, (sx - size - 2, sy - size - 2))

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

    def _hovered_item_proto(self):
        # find the item under the cursor across inventory, factory panel, and
        # world drops (in priority order). returns ItemPrototype or None.
        # held items don't get a tooltip — the cursor already shows the icon.
        if self.held_item is not None:
            return None
        mx, my = self.hover_pos

        # 1. factory panel slot
        if self.factory_panel.open and self.factory_panel.entity is not None:
            info = self.factory_panel.slot_at_pixel((mx, my))
            if info is not None:
                kind, idx = info
                ms = self.factory_panel.entity.machine_state
                slots = ms['input_slots'] if kind == 'input' else ms['output_slots']
                if slots[idx] is not None:
                    return load_item(slots[idx]['item_id'])

        # 2. inventory slot
        if self.inventory.open:
            slot_idx = self.inventory.slot_at_pixel((mx, my))
            if slot_idx is not None and self.inventory.slots[slot_idx] is not None:
                return load_item(self.inventory.slots[slot_idx]['item_id'])

        # 3. world drop under cursor
        wx, wy = self.screen.camera.screen_to_world((mx, my))
        for drop in self.world.dropped:
            if (drop.world_x <= wx < drop.world_x + DROPPED_ITEM_SIZE
                    and drop.world_y <= wy < drop.world_y + DROPPED_ITEM_SIZE):
                return load_item(drop.item_id)
        return None

    def _draw_hover_tooltip(self) -> None:
        proto = self._hovered_item_proto()
        if proto is None:
            return
        label = self.hud.font.render(proto.name, True, (245, 235, 200))
        # bottom-right corner with a subtle dark padded background
        margin = 14
        pad_x, pad_y = 10, 5
        rect = label.get_rect()
        rect.bottomright = (self.screen.width - margin, self.screen.height - margin)
        bg = pg.Surface((rect.w + pad_x * 2, rect.h + pad_y * 2), pg.SRCALPHA)
        bg.fill((0, 0, 0, 180))
        self.screen.surface.blit(bg, (rect.x - pad_x, rect.y - pad_y))
        self.screen.surface.blit(label, rect)

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
