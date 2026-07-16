
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
import skills
import player_ops


# random damage dealt per landed hit (inclusive). this is the level-1 baseline;
# a player's roll scales with their Combat level (skills.damage_range_for) and a
# mob's with its prototype combat_level.
DAMAGE_MIN, DAMAGE_MAX = 2, 3

# combat xp per point of damage dealt; health trains at a fraction of it
# (RuneScape-style). a kill pays a bonus scaled by the victim's combat_level.
COMBAT_XP_PER_DAMAGE = 4.0
HEALTH_XP_FRACTION = 0.34
KILL_XP_PER_LEVEL = 10.0

# an over-head bar stays visible this long after the last health change.
HEALTH_BAR_VISIBLE_MS = 6000

# floating damage-number lifetime + how far it rises over that life.
FLOAT_LIFETIME_MS = 850
FLOAT_RISE_PX = 30


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

    def hit(self, attacker, target, now_ms: int) -> bool:
        # `attacker` deals a level-scaled random hit to `target`: spawn a floating
        # number, mark its bar visible, award the attacker combat/health xp, and
        # remove a mob that drops to 0. returns True if the target died. no-op on
        # non-damageable targets (health None). `attacker` may be None
        # (environmental damage) -> base damage, no xp.
        if target.health is None:
            return False
        lo, hi = self._damage_range(attacker)
        amount = random.randint(lo, hi)
        target.health = max(0, target.health - amount)
        target.last_damage_ms = now_ms
        hb = target.hitbox_rect()
        self.damage_numbers.append(DamageNumber(hb.centerx, hb.top, amount, now_ms))
        died = target.health <= 0 and not target.is_player
        self._award_combat_xp(attacker, target, amount, died)
        if died:
            self._drop_loot(target)
            self.world.remove_entity(target.id)
            return True
        return False

    def _damage_range(self, attacker) -> tuple:
        # player attackers roll from their Combat level; mobs scale off their
        # prototype combat_level; None (environmental) uses the flat base.
        if attacker is not None and attacker.skills is not None:
            return skills.damage_range_for(skills.level_of(attacker.skills, 'combat'))
        if attacker is not None:
            clvl = getattr(attacker.prototype, 'combat_level', 1) or 1
            bonus = (clvl - 1) // 3
            return (DAMAGE_MIN + bonus, DAMAGE_MAX + bonus)
        return (DAMAGE_MIN, DAMAGE_MAX)

    def _award_combat_xp(self, attacker, target, amount: int, died: bool) -> None:
        # only players carry a skills component; grant combat xp scaled by the
        # damage dealt (plus a kill bonus from the victim's combat_level) and a
        # fraction of it to health. no-op for mob/None attackers.
        if attacker is None or attacker.skills is None:
            return
        combat_xp = amount * COMBAT_XP_PER_DAMAGE
        if died:
            combat_xp += (getattr(target.prototype, 'combat_level', 1) or 1) * KILL_XP_PER_LEVEL
        player_ops.grant_xp(self.world, attacker, 'combat', combat_xp)
        player_ops.grant_xp(self.world, attacker, 'health', combat_xp * HEALTH_XP_FRACTION)

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
