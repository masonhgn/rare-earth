
# shared screen-space draw glue.
#
# the widgets themselves (panels, HUD, minimap, inventory) are already shared
# classes; these are the small draw routines that single-player (game.py /
# HudOverlay / combat.py) and the net client (client.py) each used to copy.
# genuine differences — opaque vs translucent death screen, overhead-bar
# gating, held-cursor font/anchor — are handled by PARAMETERS; each caller
# keeps its own sequencing and (for the bars) its own visibility gating.

import pygame as pg

from config import TILE_LENGTH
from item import get_item_icon, load_item, format_quantity
from ui_theme import get_font
from world import world_to_tile
import interaction


_OVERHEAD_W, _OVERHEAD_H = 44, 5      # over-head health bar
_HEALTH_W, _HEALTH_H = 260, 20        # bottom-center player health bar
_BUILD_BANNER = 'BUILD MODE — click to place, G to exit'


def draw_build_highlight(world_surface, world, cam, player, held, cursor_pos) -> None:
    # green (valid) / red (invalid) tile outline under the cursor while a
    # placeable item is held. drawn on the pre-zoom world surface so it lines
    # up with the tile grid under any zoom. cursor_pos is display-space (SP:
    # game.hover_pos; client: pg.mouse.get_pos()).
    if held is None or load_item(held['item_id']).places is None:
        return
    wx, wy = cam.screen_to_world(cursor_pos)
    tile = world_to_tile((wx, wy))
    if not world.in_bounds_tile(*tile):
        return
    tx, ty = tile
    sx, sy = cam.world_to_screen((tx * TILE_LENGTH, ty * TILE_LENGTH))
    color = (80, 220, 90) if interaction.can_place(world, player, tile, held) else (220, 70, 70)
    pg.draw.rect(world_surface, color,
                 pg.Rect(round(sx), round(sy), TILE_LENGTH, TILE_LENGTH), width=3)


def draw_build_indicator(surface) -> None:
    label = get_font(20).render(_BUILD_BANNER, True, (120, 230, 130))
    surface.blit(label, label.get_rect(midtop=(surface.get_width() // 2, 12)))


def draw_held_cursor(surface, held, pos, anchor='topleft', font=None,
                     qty_color=(255, 255, 255), icon_size=None, shadow=False) -> None:
    # the drag cursor: the held stack's icon at `pos`. anchor='topleft' (SP,
    # pos = the stored screen_pos) or 'center' (client, pos = live mouse). the
    # qty label font/color + optional drop-shadow are params (the two callers
    # style it differently).
    if not held:
        return
    icon = get_item_icon(load_item(held['item_id']), size=icon_size)
    if anchor == 'center':
        x = pos[0] - icon.get_width() // 2
        y = pos[1] - icon.get_height() // 2
    else:
        x, y = pos
    surface.blit(icon, (x, y))
    if held['quantity'] > 1:
        font = font or get_font(16)
        text = format_quantity(held['quantity'])
        rect = font.render(text, True, qty_color).get_rect(
            bottomright=(x + icon.get_width(), y + icon.get_height()))
        if shadow:
            surface.blit(font.render(text, True, (0, 0, 0)), rect.move(1, 1))
        surface.blit(font.render(text, True, qty_color), rect)


def draw_health_bar(surface, player, show_number=False, font=None) -> None:
    # bottom-center player health bar. show_number renders "hp/max" (SP).
    if player is None or player.health is None:
        return
    w, h = _HEALTH_W, _HEALTH_H
    x = (surface.get_width() - w) // 2
    y = surface.get_height() - h - 14
    frac = max(0.0, player.health / player.max_health)
    pg.draw.rect(surface, (0, 0, 0), (x - 2, y - 2, w + 4, h + 4))
    pg.draw.rect(surface, (150, 40, 40), (x, y, w, h))
    if frac > 0:
        pg.draw.rect(surface, (70, 200, 80), (x, y, int(w * frac), h))
    pg.draw.rect(surface, (235, 235, 235), (x, y, w, h), width=1)
    if show_number:
        font = font or get_font(20)
        text = f'{player.health}/{player.max_health}'
        label = font.render(text, True, (245, 245, 245))
        lx = x + (w - label.get_width()) // 2
        ly = y + (h - label.get_height()) // 2
        surface.blit(font.render(text, True, (0, 0, 0)), (lx + 1, ly + 1))
        surface.blit(label, (lx, ly))


def draw_overhead_bar(surface, cam, ent) -> None:
    # one entity's over-head health bar. callers keep their own visibility
    # gating (SP: recently-damaged; client: health < max) + culling.
    hb = ent.hitbox_rect()
    bx, by = cam.world_to_screen((hb.centerx - _OVERHEAD_W / 2, hb.top - 12))
    bx, by = int(bx), int(by)
    frac = max(0.0, ent.health / ent.max_health)
    pg.draw.rect(surface, (20, 20, 24), (bx - 1, by - 1, _OVERHEAD_W + 2, _OVERHEAD_H + 2))
    pg.draw.rect(surface, (150, 40, 40), (bx, by, _OVERHEAD_W, _OVERHEAD_H))
    if frac > 0:
        pg.draw.rect(surface, (70, 200, 80), (bx, by, int(_OVERHEAD_W * frac), _OVERHEAD_H))


def draw_death_overlay(surface, opaque) -> None:
    # 'YOU DIED'. opaque=True (SP): fill black over the whole frame.
    # opaque=False (client): translucent veil over the rendered world.
    w, h = surface.get_size()
    if opaque:
        surface.fill((0, 0, 0))
    else:
        veil = pg.Surface((w, h), pg.SRCALPHA)
        veil.fill((0, 0, 0, 180))
        surface.blit(veil, (0, 0))
    label = get_font(72).render('YOU DIED', True, (170, 30, 30))
    surface.blit(label, label.get_rect(center=(w // 2, h // 2)))
