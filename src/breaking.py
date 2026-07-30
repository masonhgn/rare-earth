
# break subsystem.
#
# owns the in-progress break state (which entity/overlay tile is being
# broken, when it started, how long it takes) and the particle list
# (debris chunks spawned at completion).
#
# the game loop calls `tick(dt)` once per frame; the renderer calls
# `visuals_for_entity` / `visuals_for_overlay_tile` to get jitter+flash
# values for break-target sprites, and `queue_progress_bar` /
# `queue_particles` to draw the bar and chunks.
#
# external mutation surface is small:
#   - try_acquire_target(tile)       what's breakable here? (also used by the click)
#   - start_break(proto, tile, ...)  begin a break on the given tile
#   - cancel()                       called when the click-walk path is preempted

import math
import random
from dataclasses import dataclass

import pygame as pg

from config import TILE_LENGTH
from item import roll_drops
from prototype import load_prototype
from world import tile_center
import interaction
import crop as crop_ops
import skills
import player_ops


# --- transient visual effects: break state + procedural chunk particles ---
# all rendering is procedural (filled surfaces) — no extra sprite assets.

@dataclass
class BreakState:
    # what the player is currently breaking. one at a time.
    # entity_id=None means we're breaking the overlay tile at `tile` (ore
    # patches, etc) rather than a placed entity instance. either way the
    # `tile` is canonical for visuals and the cursor-still-on-target check.
    start_ms: int
    duration_ms: int
    tile: tuple[int, int]
    entity_id: str | None = None

    def progress(self, now_ms: int) -> float:
        if self.duration_ms <= 0:
            return 1.0
        return min(1.0, max(0.0, (now_ms - self.start_ms) / self.duration_ms))

    def is_complete(self, now_ms: int) -> bool:
        return now_ms - self.start_ms >= self.duration_ms


@dataclass
class Particle:
    # 1-tile-scale debris chunk used for break bursts. position is world-space;
    # the renderer transforms via camera. gravity is applied in tick().
    world_x: float
    world_y: float
    vx: float
    vy: float
    born_ms: int
    lifetime_ms: int
    color: tuple[int, int, int] = (60, 60, 60)
    size: int = 4

    def alive(self, now_ms: int) -> bool:
        return now_ms - self.born_ms < self.lifetime_ms

    def tick(self, dt: float) -> None:
        self.world_x += self.vx * dt
        self.world_y += self.vy * dt
        # crude gravity so chunks arc downward and land
        self.vy += 520 * dt


def spawn_break_chunks(
    world_pos: tuple[float, float],
    now_ms: int,
    count: int = 8,
    color: tuple[int, int, int] = (70, 70, 70),
) -> list[Particle]:
    # radial burst from the broken tile's center, biased slightly upward so
    # the chunks arc rather than slide flat. lifetime + size are jittered
    # so the burst doesn't look like a mechanical pattern.
    cx, cy = world_pos
    out: list[Particle] = []
    for _ in range(count):
        angle = random.uniform(0.0, 2 * math.pi)
        speed = random.uniform(80, 200)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed - 120
        lifetime = random.randint(450, 900)
        size = random.randint(3, 6)
        out.append(Particle(
            world_x=cx, world_y=cy,
            vx=vx, vy=vy,
            born_ms=now_ms, lifetime_ms=lifetime,
            color=color, size=size,
        ))
    return out


def spawn_dust_puff(world_pos: tuple[float, float], now_ms: int, count: int = 6) -> list[Particle]:
    # a light puff that kicks up + outward — e.g. when a knocked-back body
    # lands. lighter / smaller / shorter-lived than break chunks, and always
    # biased upward so it reads as dust rather than debris.
    cx, cy = world_pos
    out: list[Particle] = []
    for _ in range(count):
        angle = random.uniform(0.0, 2 * math.pi)
        speed = random.uniform(25, 80)
        out.append(Particle(
            world_x=cx, world_y=cy,
            vx=math.cos(angle) * speed,
            vy=-random.uniform(40, 110),
            born_ms=now_ms, lifetime_ms=random.randint(250, 500),
            color=(200, 190, 170), size=random.randint(2, 4),
        ))
    return out


class BreakSystem:
    def __init__(self, world, on_tile_changed=None):
        # on_tile_changed(tx, ty) fires when an overlay tile is cleared (mined)
        # so the client can refresh its minimap. None on a headless server
        # (which will turn it into a net event instead). render methods take
        # the camera as a parameter, so the sim part needs no camera/minimap.
        self.world = world
        self.on_tile_changed = on_tile_changed

        self.breaking: BreakState | None = None
        self.particles: list[Particle] = []
        # transient "Requires Mining level N" feedback set when a break is gated,
        # drained by the HUD for a brief on-screen message. (text, born_ms).
        self.gate_msg: tuple[str, int] | None = None

    def spawn_dust(self, world_pos: tuple[float, float], now_ms: int, count: int = 6) -> None:
        # kick up a small dust puff at world_pos (e.g. where a knocked-back body
        # lands, or a lighter trail while it slides). feeds the same particle
        # list as break chunks, so it ticks and renders for free.
        self.particles.extend(spawn_dust_puff(world_pos, now_ms, count=count))

    # --- public api ---

    def try_acquire_target(self, tile: tuple[int, int]):
        # (prototype, entity_id_or_None) for whatever's breakable at `tile` and
        # in reach, or None. detection is shared via interaction.breakable_at;
        # the reach gate is break-specific, as is the Mining-level gate.
        if not self.world.tile_in_reach(*tile):
            return None
        found = interaction.breakable_at(self.world, tile)
        if found is None:
            return None
        proto = found[0]
        if not interaction.can_mine(self.world.entities.get('player'), proto):
            req = getattr(proto, 'mining_level', 1) or 1
            self.gate_msg = (f'Requires Mining level {req}', pg.time.get_ticks())
            return None
        return found

    def start_break(self, proto, tile: tuple[int, int], *, entity_id: str | None) -> None:
        break_time = proto.break_time or 0.0
        # higher Mining level breaks faster (never instant if it wasn't already).
        player = self.world.entities.get('player')
        if break_time > 0 and player is not None and player.skills is not None:
            break_time *= skills.break_time_scale(skills.level_of(player.skills, 'mining'))
        center = tile_center(tile)
        if break_time <= 0:
            # instant break: finalize immediately, no BreakState lifecycle.
            if entity_id is not None:
                self._finalize_entity(entity_id, center)
            else:
                self._finalize_overlay(tile, center)
            self.breaking = None
            return
        self.breaking = BreakState(
            start_ms=pg.time.get_ticks(),
            duration_ms=int(break_time * 1000),
            tile=tile,
            entity_id=entity_id,
        )

    def cancel(self) -> None:
        self.breaking = None

    def tick(self, dt: float) -> None:
        self._tick_active_break()
        self._tick_particles(dt)

    def tick_particles(self, dt: float) -> None:
        # visual-only tick for the net client: advance particles, but DON'T run
        # the authoritative break finalize. the client owns the break timer
        # itself (client._update_break) and the server does the real clear, so
        # _tick_active_break here would double-handle it — and it assumes the
        # single-player fixed-id 'player' entity, which doesn't exist over the net.
        self._tick_particles(dt)

    # --- visuals (called by the game's render queueing) ---

    def visuals_for_entity(self, entity_id: str, now_ms: int) -> tuple[int, int, int]:
        # returns (jx, jy, flash_alpha). all zero when this entity isn't the
        # current break target, so callers can blindly apply the offsets and
        # skip the flash overlay only when alpha > 0.
        if self.breaking is None or self.breaking.entity_id != entity_id:
            return (0, 0, 0)
        return self._compute_visuals(now_ms)

    def visuals_for_overlay_tile(self, tile: tuple[int, int], now_ms: int) -> tuple[int, int, int]:
        if (self.breaking is None
                or self.breaking.entity_id is not None
                or self.breaking.tile != tile):
            return (0, 0, 0)
        return self._compute_visuals(now_ms)

    def queue_progress_bar(self, renderer, camera) -> None:
        # thin bar above the breaking tile. for entity targets, follow the
        # entity's render_offset so the bar sits above the *visible* sprite
        # rather than the bare tile (matters for offset-anchored sprites
        # like trees).
        bk = self.breaking
        if bk is None:
            return
        ox, oy = 0, 0
        is_entity = bk.entity_id is not None
        if is_entity:
            entity = self.world.entities.get(bk.entity_id)
            if entity is not None and entity.prototype.render_offset is not None:
                ox, oy = entity.prototype.render_offset
        progress = bk.progress(pg.time.get_ticks())
        tx, ty = bk.tile
        wpos = (tx * TILE_LENGTH + ox, ty * TILE_LENGTH + oy)
        # a broken ground tile sits on the tilted floor (project_ground); a
        # broken entity is drawn flat, so its bar follows the flat sprite.
        sx, sy = (camera.world_to_screen(wpos) if is_entity
                  else camera.project_ground(wpos))
        bar_w = TILE_LENGTH - 8
        bar_h = 4
        bar_x = sx + 4
        bar_y = sy - 8
        bg = pg.Surface((bar_w, bar_h), pg.SRCALPHA)
        bg.fill((0, 0, 0, 200))
        renderer.queue('highlight', bg, (bar_x, bar_y))
        fill_w = int(bar_w * progress)
        if fill_w > 0:
            fill = pg.Surface((fill_w, bar_h), pg.SRCALPHA)
            fill.fill((255, 220, 80, 240))
            renderer.queue('highlight', fill, (bar_x, bar_y))

    def queue_particles(self, renderer, camera, culling) -> None:
        if not self.particles:
            return
        now_ms = pg.time.get_ticks()
        for p in self.particles:
            if not culling.point_visible((p.world_x, p.world_y), camera.offset, size=(p.size, p.size)):
                continue
            life = (now_ms - p.born_ms) / max(p.lifetime_ms, 1)
            alpha = max(0, int(255 * (1 - life)))
            surf = pg.Surface((p.size, p.size), pg.SRCALPHA)
            surf.fill((*p.color, alpha))
            sx, sy = camera.project_ground((p.world_x, p.world_y))   # on the tilted ground
            renderer.queue('highlight', surf, (sx, sy))

    # --- private ---

    def _compute_visuals(self, now_ms: int) -> tuple[int, int, int]:
        # jitter amplitude + white flash alpha, ramping with progress so the
        # target rattles harder as it nears breaking.
        progress = self.breaking.progress(now_ms)
        amp = 1 + int(progress * 3)
        jx = random.randint(-amp, amp)
        jy = random.randint(-amp, amp)
        flash_alpha = int(25 + progress * 95)
        return (jx, jy, flash_alpha)

    def _finalize_entity(self, entity_id: str, world_pos: tuple[float, float]) -> None:
        # capture proto + crop stage before break_entity removes the instance, so
        # we can award mining/farming xp and apply the Farming yield bonus.
        ent = self.world.entities.get(entity_id)
        proto = ent.prototype if ent is not None else None
        crop = ent.components.get('crop') if ent is not None else None
        player = self.world.entities.get('player')
        farming_level = (skills.level_of(player.skills, 'farming')
                         if player is not None and player.skills is not None else 1)
        drops = self.world.break_entity(entity_id, farming_level=farming_level)
        self._distribute_drops(drops, world_pos)
        if proto is not None:
            self._grant_mining_xp(proto)
            self._grant_farming_xp(proto, crop)

    def _finalize_overlay(self, tile: tuple[int, int], world_pos: tuple[float, float]) -> None:
        tx, ty = tile
        tile_id = self.world.overlay_at(tx, ty)
        if tile_id is None:
            return
        try:
            proto = load_prototype(tile_id)
        except FileNotFoundError:
            return
        self.world.overlay_grid[ty][tx] = None
        if self.on_tile_changed is not None:
            self.on_tile_changed(tx, ty)
        self._distribute_drops(roll_drops(proto.drops), world_pos)
        self._grant_mining_xp(proto)

    def _grant_mining_xp(self, proto) -> None:
        # award the breaking player this prototype's mining xp (default 0, so
        # only ore/rock protos that set mining_xp pay out).
        xp = getattr(proto, 'mining_xp', 0.0) or 0.0
        if xp > 0:
            player = self.world.entities.get('player')
            player_ops.grant_xp(self.world, player, 'mining', xp)

    def _grant_farming_xp(self, proto, crop) -> None:
        # farming xp only for harvesting a MATURE crop (an immature pull just
        # returns the seed, so it shouldn't train the skill).
        if proto.crop is None or crop is None:
            return
        xp = getattr(proto, 'farming_xp', 0.0) or 0.0
        if xp > 0 and crop_ops.is_mature(proto.crop, crop['stage']):
            player = self.world.entities.get('player')
            player_ops.grant_xp(self.world, player, 'farming', xp)

    def _distribute_drops(self, drops: list[tuple[str, int]], world_pos: tuple[float, float]) -> None:
        # drops spawn in the world (visible at the break site), auto-pickup
        # in Game._update sweeps them into inventory when the player walks
        # over them.
        for item_id, qty in drops:
            self.world.spawn_dropped_item(item_id, qty, world_pos)
        self.particles.extend(spawn_break_chunks(world_pos, pg.time.get_ticks()))

    def _tick_active_break(self) -> None:
        bk = self.breaking
        if bk is None:
            return
        # target liveness
        if bk.entity_id is not None:
            if bk.entity_id not in self.world.entities:
                self.breaking = None
                return
        else:
            if self.world.overlay_at(*bk.tile) is None:
                self.breaking = None
                return
        if not self.world.tile_in_reach(*bk.tile):
            self.breaking = None
            return
        now_ms = pg.time.get_ticks()
        if bk.is_complete(now_ms):
            center = tile_center(bk.tile)
            if bk.entity_id is not None:
                self._finalize_entity(bk.entity_id, center)
            else:
                self._finalize_overlay(bk.tile, center)
            self.breaking = None

    def _tick_particles(self, dt: float) -> None:
        if not self.particles:
            return
        now_ms = pg.time.get_ticks()
        for p in self.particles:
            p.tick(dt)
        self.particles = [p for p in self.particles if p.alive(now_ms)]
