
# player kinematics: path following, facing/animation, bounds + collision.
#
# pure functions over (player, world, dt) so Game._update just sequences
# them. nothing here holds state — the player entity and world carry it.

from config import TILE_LENGTH


# knockback imparted to a target when a hit lands; decays in apply_knockback.
# total travel ~= KNOCKBACK_SPEED / KNOCKBACK_DECAY, tuned here to ~one tile.
KNOCKBACK_DECAY = 14.0                            # per-second decay (higher = fades faster)
KNOCKBACK_SPEED = TILE_LENGTH * KNOCKBACK_DECAY   # initial px/s; ~1 tile of total travel


def _blocked(world, entity) -> bool:
    # True if `entity`'s hitbox overlaps a solid (tile-locked) entity's
    # footprint, OR another living (non-tile-locked) entity's hitbox. living
    # things — the player and mobs — don't pass through each other or solids.
    # cheap n^2 scan; fine for one player + a handful of mobs.
    hb = entity.hitbox_rect()
    for other in world.entities.values():
        if other is entity:
            continue
        proto = other.prototype
        if proto.solid and hb.colliderect(other.collision_rect()):
            return True
        if not proto.tile_locked and hb.colliderect(other.hitbox_rect()):
            return True
    return False


def move_axis(world, entity, dx: float, dy: float) -> tuple[float, float]:
    # move `entity` by (dx, dy), each axis independently reverted if it lands
    # the hitbox in a blocked spot, so it slides along walls / other bodies
    # instead of sticking or overlapping. returns the (dx, dy) actually applied.
    moved_x = moved_y = 0.0
    if dx:
        entity.world_x += dx
        if _blocked(world, entity):
            entity.world_x -= dx
        else:
            moved_x = dx
    if dy:
        entity.world_y += dy
        if _blocked(world, entity):
            entity.world_y -= dy
        else:
            moved_y = dy
    return moved_x, moved_y


def _solid_blocked(world, entity) -> bool:
    # like _blocked but solids only — used by separation, which deliberately
    # ignores living-vs-living overlap (that's exactly what it's resolving).
    hb = entity.hitbox_rect()
    for other in world.entities.values():
        if other is entity:
            continue
        if other.prototype.solid and hb.colliderect(other.collision_rect()):
            return True
    return False


def _try_shift(world, entity, dx: float, dy: float) -> bool:
    # shift `entity` by (dx, dy) unless it would land inside a solid. returns
    # whether the shift was applied.
    if dx == 0.0 and dy == 0.0:
        return True
    entity.world_x += dx
    entity.world_y += dy
    if _solid_blocked(world, entity):
        entity.world_x -= dx
        entity.world_y -= dy
        return False
    return True


def _resolve_overlap(world, a, b) -> None:
    # if a and b's hitboxes overlap, shove them fully apart along the axis of
    # least penetration. prefers to displace the mob, not the player; only moves
    # the player if the mob is wedged against a solid.
    ra, rb = a.hitbox_rect(), b.hitbox_rect()
    ox = min(ra.right, rb.right) - max(ra.left, rb.left)
    oy = min(ra.bottom, rb.bottom) - max(ra.top, rb.top)
    if ox <= 0 or oy <= 0:
        return  # not actually overlapping
    if ox <= oy:
        dxa, dya = (ox if ra.centerx >= rb.centerx else -ox), 0.0
    else:
        dxa, dya = 0.0, (oy if ra.centery >= rb.centery else -oy)
    # (dxa, dya) moves `a` away from `b`; negated moves `b` away from `a`.
    if a.is_player:
        if not _try_shift(world, b, -dxa, -dya):
            _try_shift(world, a, dxa, dya)
    else:
        if not _try_shift(world, a, dxa, dya):
            _try_shift(world, b, -dxa, -dya)


def separate_living(world) -> None:
    # push apart any overlapping living (non-tile-locked) entities so nothing
    # ever gets stuck inside another body — e.g. a mob spawned on the player.
    # runs once per frame and resolves the full overlap immediately, so an
    # accidental overlap never deadlocks (move_axis would revert every escape).
    living = [e for e in world.entities.values() if not e.prototype.tile_locked]
    for i in range(len(living)):
        for j in range(i + 1, len(living)):
            _resolve_overlap(world, living[i], living[j])


def knock_back(attacker, target) -> None:
    # send `target` flying away from `attacker`. the impulse is integrated +
    # decayed each frame by apply_knockback. used when an attack lands.
    ax, ay = attacker.center
    tx, ty = target.center
    dx, dy = tx - ax, ty - ay
    dist = (dx * dx + dy * dy) ** 0.5
    if dist < 1e-6:
        dx, dy, dist = 1.0, 0.0, 1.0   # exact overlap: shove right arbitrarily
    target.knockback_x = dx / dist * KNOCKBACK_SPEED
    target.knockback_y = dy / dist * KNOCKBACK_SPEED


def apply_knockback(world, entity, dt: float) -> bool:
    # integrate + decay an entity's knockback impulse this frame, collision-
    # aware so it can't shove the target through walls or other bodies. returns
    # True on the frame the impulse runs out (the target "lands"), so callers
    # can kick up dust there.
    kx, ky = entity.knockback_x, entity.knockback_y
    if kx == 0.0 and ky == 0.0:
        return False
    move_axis(world, entity, kx * dt, ky * dt)
    decay = max(0.0, 1.0 - KNOCKBACK_DECAY * dt)
    entity.knockback_x = kx * decay if abs(kx * decay) > 1.0 else 0.0
    entity.knockback_y = ky * decay if abs(ky * decay) > 1.0 else 0.0
    return entity.knockback_x == 0.0 and entity.knockback_y == 0.0


def follow_path(entity, world, dt: float, speed: float | None = None) -> tuple[float, float]:
    # walk `entity` along entity.path at `speed` px/s (defaults to the entity's
    # prototype speed), collision-aware so it slides along / stops at solids and
    # other living things. returns the total (dx, dy) actually applied, used by
    # update_player_animation. generic over any entity with
    # .path/.world_x/.world_y/.prototype, so both the player and MobSystem use it.
    if speed is None:
        speed = entity.prototype.speed or 0.0
    step_remaining = speed * dt
    total_dx = total_dy = 0.0
    while entity.path and step_remaining > 0:
        wp_tx, wp_ty = entity.path[0]
        target_x = wp_tx * TILE_LENGTH + TILE_LENGTH / 2
        target_y = wp_ty * TILE_LENGTH + TILE_LENGTH / 2
        center_x, center_y = entity.center
        dx = target_x - center_x
        dy = target_y - center_y
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < 4:
            entity.path.pop(0)
            continue
        leg = min(step_remaining, dist)
        mvx, mvy = move_axis(world, entity, dx / dist * leg, dy / dist * leg)
        total_dx += mvx
        total_dy += mvy
        step_remaining -= leg
        if abs(mvx) < 1e-9 and abs(mvy) < 1e-9:
            break  # fully blocked this frame; stop to avoid spinning
    return total_dx, total_dy


def update_player_animation(player, dx: float, dy: float) -> None:
    # single canonical animation update, run after movement is resolved.
    # facing follows the dominant axis; ties (|dx| == |dy|, i.e. diagonal
    # movement) go to horizontal — preserves the previous left/right
    # preference and avoids flicker when the path follower bounces between
    # near-equal components.
    if player.anim is None:
        return
    if dx == 0 and dy == 0:
        player.anim.set_state('idle')
        return
    # 0.5 threshold avoids flipping facing for sub-pixel rounding noise.
    if abs(dx) >= abs(dy):
        if dx > 0.5:
            player.anim.set_state('walking_right')
        elif dx < -0.5:
            player.anim.set_state('walking_left')
    else:
        if dy > 0.5:
            player.anim.set_state('walking_down')
        elif dy < -0.5:
            player.anim.set_state('walking_up')


def clamp_to_bounds(world, entity) -> None:
    # keep an entity's *hitbox* inside the map rectangle. clamping the full
    # 128x128 sprite frame would stop it ~40px before the visible body reaches
    # the edge (most of the frame is transparent padding). matches hitbox_rect.
    sprite_w, sprite_h = entity.sprite_dims
    hitbox_w, hitbox_h = entity.prototype.hitbox or (sprite_w, sprite_h)
    hx_off = (sprite_w - hitbox_w) / 2
    hy_off = sprite_h - hitbox_h
    map_w = world.width * TILE_LENGTH
    map_h = world.height * TILE_LENGTH
    entity.world_x = max(-hx_off, min(entity.world_x, map_w - hx_off - hitbox_w))
    entity.world_y = max(-hy_off, min(entity.world_y, map_h - hy_off - hitbox_h))


def clamp_player_to_bounds(world) -> None:
    # client convenience: clamp the local player.
    clamp_to_bounds(world, world.get_player())
