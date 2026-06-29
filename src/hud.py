
# diagnostic overlay: fps, frame time, entity count. toggled by F3.
# HudOverlay (bottom of file) owns the rest of the screen-space drawing:
# day counter, minimap, hover tooltip and the cursor-held item.

import pygame as pg

from config import TILE_LENGTH, DROPPED_ITEM_SIZE
from item import load_item, format_quantity, get_item_icon
from ui_theme import COLOR_SLOT_QTY_TEXT, get_font


class Hud:
    def __init__(self):
        self.visible = True
        self.font = get_font(20)
        # bigger font for the day counter so it reads at a glance even
        # without leaning toward the screen
        self.day_font = get_font(28)

    def toggle(self) -> None:
        self.visible = not self.visible

    def render(self, surface: pg.Surface, *, fps: float, frame_ms: float, n_entities: int, n_dropped: int) -> None:
        if not self.visible:
            return
        lines = [
            f'fps {fps:.0f}  frame {frame_ms:.1f}ms',
            f'entities {n_entities}  drops {n_dropped}',
            'wasd move  b inventory  lmb break  rmb place  f2 display mode  f3 hud',
        ]
        y = 6
        for line in lines:
            label = self.font.render(line, True, (235, 235, 235))
            # 1-px black drop shadow for readability over any background
            shadow = self.font.render(line, True, (0, 0, 0))
            surface.blit(shadow, (7, y + 1))
            surface.blit(label, (6, y))
            y += label.get_height() + 2

    def render_day_counter(self, surface: pg.Surface, *, day: int) -> None:
        # always visible regardless of self.visible — the day counter is
        # core gameplay info, not diagnostic. anchored top-right with the
        # same 1-px drop shadow as the diagnostic block.
        text = f'day {day}'
        label = self.day_font.render(text, True, (235, 235, 235))
        shadow = self.day_font.render(text, True, (0, 0, 0))
        x = surface.get_width() - label.get_width() - 10
        y = 6
        surface.blit(shadow, (x + 1, y + 1))
        surface.blit(label, (x, y))


class HudOverlay:
    # screen-space overlays drawn directly on the display surface, on top of
    # the flushed world. holds a Game ref and reads display state off it
    # (view-only). split into two passes because the inventory + modal panels
    # draw *between* the base hud and the cursor layer:
    #   render_base()   -> diagnostics, day counter, minimap
    #   render_cursor() -> hover tooltip, held item (always on top)
    def __init__(self, game):
        self.game = game

    def render_base(self) -> None:
        self._draw_hud()
        self._draw_minimap()
        self._draw_player_health()

    def render_cursor(self) -> None:
        self._draw_hover_tooltip()
        self._draw_held_item()

    def _draw_hud(self) -> None:
        game = self.game
        # clock.get_fps() is smoothed over the last 10 frames
        game.hud.render(
            game.screen.surface,
            fps=game.clock.get_fps(),
            frame_ms=game.dt * 1000,
            n_entities=len(game.world.entities),
            n_dropped=len(game.world.dropped),
        )
        # day counter is always visible regardless of F3 toggle
        game.hud.render_day_counter(game.screen.surface, day=game.day_clock.day)

    def _draw_minimap(self) -> None:
        game = self.game
        player = game.world.get_player()
        # use the player's visual center, not the sprite top-left, so the
        # marker tracks where the character actually is.
        sw, sh = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
        center = (player.world_x + sw / 2, player.world_y + sh / 2)
        game.minimap.render(
            game.screen.surface,
            (game.screen.width, game.screen.height),
            game.screen.camera.offset,
            center,
        )

    def _draw_player_health(self) -> None:
        # the player's own health, bottom-center: a green fill over a red
        # background, with the numeric value. always visible.
        game = self.game
        player = game.world.get_player()
        if player.health is None:
            return
        surf = game.screen.surface
        w, h = 260, 20
        x = (game.screen.width - w) // 2
        y = game.screen.height - h - 14
        frac = max(0.0, player.health / player.max_health)
        pg.draw.rect(surf, (0, 0, 0), (x - 2, y - 2, w + 4, h + 4))
        pg.draw.rect(surf, (150, 40, 40), (x, y, w, h))
        if frac > 0:
            pg.draw.rect(surf, (70, 200, 80), (x, y, int(w * frac), h))
        pg.draw.rect(surf, (235, 235, 235), (x, y, w, h), width=1)
        text = f'{player.health}/{player.max_health}'
        label = game.hud.font.render(text, True, (245, 245, 245))
        shadow = game.hud.font.render(text, True, (0, 0, 0))
        lx = x + (w - label.get_width()) // 2
        ly = y + (h - label.get_height()) // 2
        surf.blit(shadow, (lx + 1, ly + 1))
        surf.blit(label, (lx, ly))

    def _hovered_item_proto(self):
        # find the item under the cursor across inventory, factory panel, and
        # world drops (in priority order). returns ItemPrototype or None.
        # held items don't get a tooltip — the cursor already shows the icon.
        game = self.game
        if game.held_item is not None:
            return None
        mx, my = game.hover_pos

        # 1. factory panel slot
        if game.factory_panel.open and game.factory_panel.entity is not None:
            info = game.factory_panel.slot_at_pixel((mx, my))
            if info is not None:
                kind, idx = info
                ms = game.factory_panel.entity.machine_state
                slots = ms['input_slots'] if kind == 'input' else ms['output_slots']
                if slots[idx] is not None:
                    return load_item(slots[idx]['item_id'])

        # 2. inventory slot
        if game.inventory.open:
            slot_idx = game.inventory.slot_at_pixel((mx, my))
            if slot_idx is not None and game.inventory.slots[slot_idx] is not None:
                return load_item(game.inventory.slots[slot_idx]['item_id'])

        # 3. world drop under cursor
        wx, wy = game.screen.camera.screen_to_world((mx, my))
        for drop in game.world.dropped:
            if (drop.world_x <= wx < drop.world_x + DROPPED_ITEM_SIZE
                    and drop.world_y <= wy < drop.world_y + DROPPED_ITEM_SIZE):
                return load_item(drop.item_id)
        return None

    def _draw_hover_tooltip(self) -> None:
        game = self.game
        proto = self._hovered_item_proto()
        if proto is None:
            return
        label = game.hud.font.render(proto.name, True, (245, 235, 200))
        # bottom-right corner with a subtle dark padded background
        margin = 14
        pad_x, pad_y = 10, 5
        rect = label.get_rect()
        rect.bottomright = (game.screen.width - margin, game.screen.height - margin)
        bg = pg.Surface((rect.w + pad_x * 2, rect.h + pad_y * 2), pg.SRCALPHA)
        bg.fill((0, 0, 0, 180))
        game.screen.surface.blit(bg, (rect.x - pad_x, rect.y - pad_y))
        game.screen.surface.blit(label, rect)

    def _draw_held_item(self) -> None:
        game = self.game
        if game.held_item is None:
            return
        proto = load_item(game.held_item['item_id'])
        img = get_item_icon(proto)
        pos = game.held_item['screen_pos']
        game.screen.surface.blit(img, pos)
        if game.held_item['quantity'] > 1:
            label = game.inventory.font.render(
                format_quantity(game.held_item['quantity']), True, COLOR_SLOT_QTY_TEXT,
            )
            label_rect = label.get_rect(bottomright=(pos[0] + img.get_width(), pos[1] + img.get_height()))
            game.screen.surface.blit(label, label_rect)

