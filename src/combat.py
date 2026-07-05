
# combat: health, damage, floating "-N" numbers, and over-head health bars.
#
# health itself lives on Entity (entity.health / .max_health / .last_damage_ms).
# CombatSystem applies damage when a hit lands, spawns the floating numbers,
# removes mobs that drop to 0, and draws the world-space overlays (bars over
# recently-hit entities + the rising damage numbers). the player's own health
# bar is drawn screen-space by the hud (bottom of the screen).

import random
from dataclasses import dataclass

import pygame as pg

from item import roll_drops
from ui_theme import get_font
import hud_render


# random damage dealt per landed hit (inclusive).
DAMAGE_MIN, DAMAGE_MAX = 2, 3

# an over-head bar stays visible this long after the last health change.
HEALTH_BAR_VISIBLE_MS = 6000

# floating damage-number lifetime + how far it rises over that life.
FLOAT_LIFETIME_MS = 850
FLOAT_RISE_PX = 30

_BAR_W = 44
_BAR_H = 5


@dataclass
class DamageNumber:
    world_x: float
    world_y: float
    amount: int
    born_ms: int


class CombatSystem:
    def __init__(self, world) -> None:
        self.world = world
        self.damage_numbers: list[DamageNumber] = []

    def hit(self, target, now_ms: int) -> bool:
        # deal a random DAMAGE_MIN..MAX to `target`, spawn a floating number,
        # mark its bar visible, and remove a mob that drops to 0. returns True
        # if the target died. no-op on non-damageable entities (health None).
        if target.health is None:
            return False
        amount = random.randint(DAMAGE_MIN, DAMAGE_MAX)
        target.health = max(0, target.health - amount)
        target.last_damage_ms = now_ms
        hb = target.hitbox_rect()
        self.damage_numbers.append(DamageNumber(hb.centerx, hb.top, amount, now_ms))
        if target.health <= 0 and not target.is_player:
            self._drop_loot(target)
            self.world.remove_entity(target.id)
            return True
        return False

    def _drop_loot(self, entity) -> None:
        # spawn a dying mob's loot (prototype.drops) where it fell.
        hb = entity.hitbox_rect()
        pos = (hb.centerx, hb.centery)
        for item_id, qty in roll_drops(entity.prototype.drops):
            self.world.spawn_dropped_item(item_id, qty, pos)

    def tick(self, now_ms: int) -> None:
        self.damage_numbers = [
            d for d in self.damage_numbers if now_ms - d.born_ms < FLOAT_LIFETIME_MS
        ]

    # --- world-space rendering (called after the world layers are flushed) ---

    def render_world(self, surface: pg.Surface, cam, culling, now_ms: int) -> None:
        self._render_health_bars(surface, cam, culling, now_ms)
        self._render_damage_numbers(surface, cam, now_ms)

    def _render_health_bars(self, surface, cam, culling, now_ms: int) -> None:
        for ent in self.world.entities.values():
            # player uses the bottom-of-screen bar; only show over-head bars
            # for other living things, and only while recently damaged.
            if ent.health is None or ent.is_player:
                continue
            # hidden until first damaged, then visible for 6s after each change.
            if ent.last_damage_ms is None or now_ms - ent.last_damage_ms >= HEALTH_BAR_VISIBLE_MS:
                continue
            hb = ent.hitbox_rect()
            if not culling.point_visible((hb.x, hb.y), cam.offset, size=(hb.w, hb.h)):
                continue
            hud_render.draw_overhead_bar(surface, cam, ent)

    def _render_damage_numbers(self, surface, cam, now_ms: int) -> None:
        font = get_font(20)
        for d in self.damage_numbers:
            t = (now_ms - d.born_ms) / FLOAT_LIFETIME_MS
            if t >= 1.0:
                continue
            alpha = max(0, int(255 * (1.0 - t)))
            sx, sy = cam.world_to_screen((d.world_x, d.world_y - FLOAT_RISE_PX * t))
            text = f'-{d.amount}'
            label = font.render(text, True, (255, 110, 90))
            shadow = font.render(text, True, (0, 0, 0))
            label.set_alpha(alpha)
            shadow.set_alpha(alpha)
            ox = int(sx) - label.get_width() // 2
            oy = int(sy) - 24
            surface.blit(shadow, (ox + 1, oy + 1))
            surface.blit(label, (ox, oy))
