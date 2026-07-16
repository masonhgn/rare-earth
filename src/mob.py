
# mob ai: per-frame wander + player-chase driven by A* pathfinding.
#
# MobSystem.tick(dt) advances every entity carrying a 'mob' component.
# detection is straight-line distance (aggro / deaggro radii); *movement*
# routes through pathfinding.find_path and is walked with
# movement.follow_path, reusing the same entity.path the player uses. facing /
# animation is updated via movement.update_player_animation, so a mob with an
# animation spec (the goblin) plays the right directional strip while a
# placeholder mob without one is a harmless no-op.
#
# per-mob state lives in components['mob'] (see entity.Entity):
#   'wander' -> stroll to a random nearby walkable tile, pause, repeat.
#               flips to 'chase' when a hostile mob's target enters aggro_radius.
#   'chase'  -> re-path toward the player a few times a second and follow it.
#               flips back to 'wander' past deaggro_radius (the gap between the
#               two radii is hysteresis so it doesn't flip-flop at the boundary).

import math
import random

import pygame as pg

from config import TILE_LENGTH
from pathfinding import find_path
from world import world_to_tile
import movement


# how often a chasing mob recomputes its route to the (moving) player.
# re-pathing every frame is wasteful and jittery; ~2.5x/sec reads as smooth
# pursuit and stays cheap on the 60x60 grid even with several mobs.
CHASE_REPATH_SEC = 0.4

# how far (in tiles) a wandering mob looks when picking its next destination.
WANDER_TILE_RADIUS = 6

# how long a mob stands still between wander legs (seconds, randomized).
# higher = loiters more / strolls less. tune to taste.
WANDER_PAUSE_RANGE = (1.5, 4.0)


def _nearest_player(players, x: float, y: float):
    # the player entity closest to (x, y) by squared center distance, or None.
    # a mob aggros/attacks its nearest player (shared server: many, or none).
    best, best_d = None, None
    for p in players:
        px, py = p.center
        d = (px - x) ** 2 + (py - y) ** 2
        if best is None or d < best_d:
            best, best_d = p, d
    return best


class MobSystem:
    def __init__(self, world, break_system, combat) -> None:
        self.world = world
        self.break_system = break_system
        self.combat = combat

    def tick(self, dt: float) -> None:
        players = self.world.players()
        now_ms = pg.time.get_ticks()
        for mob in self.world.entities_with('mob'):
            spec = mob.prototype.mob
            ms = mob.components['mob']
            ms['attack_cd'] = max(0.0, ms['attack_cd'] - dt)
            mcx, mcy = mob.center

            # target the nearest player (shared server: many players, or none).
            target = _nearest_player(players, mcx, mcy)
            if target is not None:
                pcx, pcy = target.center
                dist = math.hypot(pcx - mcx, pcy - mcy)
            else:
                pcx = pcy = 0.0
                dist = float('inf')

            # --- state transitions (hysteresis; only hostile mobs chase) ---
            if (target is not None and spec.get('hostile')
                    and ms['state'] == 'wander' and dist <= spec['aggro_radius']):
                ms['state'] = 'chase'
                mob.path = []
                ms['repath_cd'] = 0.0
            elif ms['state'] == 'chase' and (target is None or dist > spec['deaggro_radius']):
                ms['state'] = 'wander'
                mob.path = []

            # --- act (movement) ---
            if ms['state'] == 'chase' and target is not None:
                player_tile = world_to_tile((pcx, pcy))
                moved = self._chase(mob, ms, player_tile, (mcx, mcy), dt)
            else:
                moved = self._wander(mob, ms, spec['wander_speed'], dt)

            # --- fight back: swing within melee range on a randomized cadence,
            # facing the nearest player and knocking it back when it lands.
            if target is not None:
                self._maybe_attack(mob, ms, spec, dist, target, pcx, mcx, now_ms)

            # facing/animation: hold a one-shot swing while it plays; otherwise
            # follow the movement vector (idle on zero).
            anim = mob.anim
            if anim is None or not anim.oneshot or anim.finished:
                movement.update_player_animation(mob, *moved)

            # decay any knockback this mob is under (e.g. from a player hit);
            # trail dust while it slides, and a bigger puff where it lands.
            sliding = mob.knockback_x or mob.knockback_y
            landed = movement.apply_knockback(self.world, mob, dt)
            if sliding:
                hb = mob.hitbox_rect()
                pos = (hb.centerx, hb.bottom)
                if landed:
                    self.break_system.spawn_dust(pos, pg.time.get_ticks())
                elif random.random() < 0.5:
                    self.break_system.spawn_dust(pos, pg.time.get_ticks(), count=2)

    def _maybe_attack(self, mob, ms, spec, dist, player, pcx, mcx, now_ms) -> None:
        # melee swing when in range + off cooldown: face the player, knock them
        # back, and deal damage. cooldown is the spec period jittered +-20%.
        # the cooldown alone gates re-attacks — we intentionally do NOT gate on
        # the animation finishing, since anim only advances in the renderer and
        # a headless server would otherwise attack just once.
        if not spec.get('hostile'):
            return
        attack_range = spec.get('attack_range', 0)
        if attack_range <= 0 or dist > attack_range or ms['attack_cd'] > 0.0:
            return
        if mob.anim is not None:  # visual only; damage/knockback don't need it
            facing = 'left' if pcx < mcx else 'right'
            mob.anim.play_once('attacking_' + facing, now_ms)
        movement.knock_back(mob, player)
        self.combat.hit(mob, player, now_ms)
        ms['attack_cd'] = spec.get('attack_period', 1.0) * random.uniform(0.8, 1.2)

    # --- behaviors ---

    def _chase(self, mob, ms, player_tile, mob_center, dt) -> tuple[float, float]:
        ms['repath_cd'] -= dt
        if ms['repath_cd'] <= 0.0 or not mob.path:
            goal = self.world.nearest_walkable(*player_tile)
            mob.path = (find_path(self.world, world_to_tile(mob_center), goal) or []) if goal else []
            ms['repath_cd'] = CHASE_REPATH_SEC
        return movement.follow_path(mob, self.world, dt, speed=mob.prototype.speed)

    def _wander(self, mob, ms, speed, dt) -> tuple[float, float]:
        if ms['wander_pause'] > 0.0:
            ms['wander_pause'] -= dt
            return (0.0, 0.0)
        if not mob.path:
            mtx, mty = world_to_tile(mob.center)
            r = WANDER_TILE_RADIUS
            target = self.world.nearest_walkable(
                mtx + random.randint(-r, r), mty + random.randint(-r, r)
            )
            mob.path = (find_path(self.world, (mtx, mty), target) or []) if target else []
            if not mob.path:
                ms['wander_pause'] = random.uniform(*WANDER_PAUSE_RANGE)
                return (0.0, 0.0)
        moved = movement.follow_path(mob, self.world, dt, speed=speed)
        if not mob.path:  # reached the end of the route
            ms['wander_pause'] = random.uniform(*WANDER_PAUSE_RANGE)
        return moved
