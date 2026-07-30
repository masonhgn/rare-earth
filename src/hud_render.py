
# shared screen-space draw glue.
#
# the widgets themselves (panels, HUD, minimap, inventory) are already shared
# classes; these are the small draw routines that single-player (game.py /
# HudOverlay / combat.py) and the net client (client.py) each used to copy.
# genuine differences — opaque vs translucent death screen, overhead-bar
# gating, held-cursor font/anchor — are handled by PARAMETERS; each caller
# keeps its own sequencing and (for the bars) its own visibility gating.

import random

import pygame as pg

from config import TILE_LENGTH
from item import get_item_icon, load_item, format_quantity
from ui_theme import get_font
from world import world_to_tile
import interaction
import skills


_OVERHEAD_W, _OVERHEAD_H = 44, 5      # over-head health bar
_HEALTH_W, _HEALTH_H = 260, 20        # bottom-center player health bar
_BUILD_BANNER = 'BUILD MODE — click to place, G to exit'

# player health-bar "rattle": px of jitter right after a hit, decaying to 0.
RATTLE_MS = 350
RATTLE_PX = 5


def health_bar_shake(hit_ms, now_ms) -> float:
    # px of jitter for the bottom player bar, decaying over RATTLE_MS after the
    # last hit at hit_ms. callers pass whatever timestamp marks "just took
    # damage" (SP: player.last_damage_ms; client: a tracked hp-drop time).
    if hit_ms is None:
        return 0.0
    t = (now_ms - hit_ms) / RATTLE_MS
    return 0.0 if t < 0 or t >= 1 else (1.0 - t) * RATTLE_PX


def draw_build_highlight(world_surface, world, cam, player, held, cursor_pos) -> None:
    # green (valid) / red (invalid) tile outline under the cursor while a
    # placeable item is held. drawn on the pre-zoom world surface so it lines
    # up with the tile grid under any zoom. cursor_pos is display-space (SP:
    # game.hover_pos; client: pg.mouse.get_pos()).
    if held is None or load_item(held['item_id']).places is None:
        return
    wx, wy = cam.pick(cursor_pos)   # perspective-aware cursor -> tile
    tile = world_to_tile((wx, wy))
    if not world.in_bounds_tile(*tile):
        return
    tx, ty = tile
    color = (80, 220, 90) if interaction.can_place(world, player, tile, held) else (220, 70, 70)
    # outline the tile on the tilted ground: project all four corners so the
    # highlight is a trapezoid matching the warped grid, not a flat square.
    corners = [cam.project_ground((cx * TILE_LENGTH, cy * TILE_LENGTH))
               for cx, cy in ((tx, ty), (tx + 1, ty), (tx + 1, ty + 1), (tx, ty + 1))]
    pg.draw.polygon(world_surface, color,
                    [(round(x), round(y)) for x, y in corners], width=3)


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


def draw_health_bar(surface, player, show_number=False, font=None, shake=0.0) -> None:
    # bottom-center player health bar. show_number renders "hp/max" (SP).
    # shake (px) jitters the whole bar for a moment after a hit — see
    # health_bar_shake() for the decay the callers feed in.
    if player is None or player.health is None:
        return
    w, h = _HEALTH_W, _HEALTH_H
    x = (surface.get_width() - w) // 2
    y = surface.get_height() - h - 14
    if shake:
        s = int(shake)
        x += random.randint(-s, s)
        y += random.randint(-s, s)
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


# skill level-up toasts: a queued line ("Mining Level 12!") that rises + fades.
TOAST_MS = 2600
_TOAST_RISE = 20
# gated-break message ("Requires Mining level N") lifetime.
GATE_MSG_MS = 1500


class LevelUpToasts:
    # per-view toast queue. drains world.pending_level_ups (filled by
    # player_ops.grant_xp) into timed lines and renders them stacked, newest at
    # the bottom. one instance per HUD (single-player Game + the net client).
    def __init__(self) -> None:
        self._items: list[list] = []      # [text, born_ms]

    def pump(self, world, now_ms: int) -> None:
        pending = getattr(world, 'pending_level_ups', None)
        if pending:
            for skill, level in pending:
                self._items.append([f'{skills.display_name(skill)} Level {level}!', now_ms])
            pending.clear()
        self._items = [it for it in self._items if now_ms - it[1] < TOAST_MS]

    def render(self, surface: pg.Surface, now_ms: int) -> None:
        if not self._items:
            return
        font = get_font(26)
        cx = surface.get_width() // 2
        base_y = surface.get_height() // 3
        for i, (text, born) in enumerate(reversed(self._items)):
            t = (now_ms - born) / TOAST_MS
            alpha = max(0, int(255 * (1 - t)))
            label = font.render(text, True, (255, 225, 120))
            shadow = font.render(text, True, (0, 0, 0))
            label.set_alpha(alpha)
            shadow.set_alpha(alpha)
            rect = label.get_rect(center=(cx, base_y - i * 34 - int(_TOAST_RISE * t)))
            surface.blit(shadow, rect.move(2, 2))
            surface.blit(label, rect)


def draw_gate_message(surface, break_system, now_ms: int) -> None:
    # brief centered "Requires Mining level N" when a break was level-gated.
    # clears itself once the message has faded out.
    msg = getattr(break_system, 'gate_msg', None)
    if not msg:
        return
    text, born = msg
    age = now_ms - born
    if age >= GATE_MSG_MS:
        break_system.gate_msg = None
        return
    alpha = max(0, int(255 * (1 - age / GATE_MSG_MS)))
    font = get_font(22)
    label = font.render(text, True, (240, 160, 90))
    shadow = font.render(text, True, (0, 0, 0))
    label.set_alpha(alpha)
    shadow.set_alpha(alpha)
    rect = label.get_rect(center=(surface.get_width() // 2, surface.get_height() // 2 - 60))
    surface.blit(shadow, rect.move(1, 1))
    surface.blit(label, rect)


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
