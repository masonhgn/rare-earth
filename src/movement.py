
# player kinematics: path following, facing/animation, bounds + collision.
#
# pure functions over (player, world, dt) so Game._update just sequences
# them. nothing here holds state — the player entity and world carry it.

from config import TILE_LENGTH


def player_collides_with_solid(world) -> bool:
    # is the player's hitbox overlapping any solid entity's footprint?
    # cheap n² scan over world.entities — fine until we have many solids
    # on screen; switch to a spatial index then.
    hb = world.get_player().hitbox_rect()
    for entity in world.entities.values():
        if entity.prototype.solid and hb.colliderect(entity.collision_rect()):
            return True
    return False


def follow_path(entity, world, dt: float, speed: float | None = None) -> tuple[float, float]:
    # walk `entity` along entity.path at `speed` px/s (defaults to the
    # entity's prototype speed). consumes as much of this frame's step as
    # possible — if it arrives at a waypoint with leftover step, the loop
    # continues to the next waypoint in the same frame (so animation never
    # sees a 0-vector mid-walk just because a waypoint flipped). returns the
    # total (dx, dy) actually applied, used by update_player_animation.
    # generic over any entity with .path/.world_x/.world_y/.prototype, so
    # both the player and MobSystem drive it.
    sprite_w, sprite_h = entity.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
    if speed is None:
        speed = entity.prototype.speed or 0.0
    step_remaining = speed * dt
    total_dx = total_dy = 0.0
    while entity.path and step_remaining > 0:
        wp_tx, wp_ty = entity.path[0]
        target_x = wp_tx * TILE_LENGTH + TILE_LENGTH / 2
        target_y = wp_ty * TILE_LENGTH + TILE_LENGTH / 2
        center_x = entity.world_x + sprite_w / 2
        center_y = entity.world_y + sprite_h / 2
        dx = target_x - center_x
        dy = target_y - center_y
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < 4:
            entity.path.pop(0)
            continue
        if step_remaining >= dist:
            # cover the rest of the leg this frame; pop and keep going.
            entity.world_x += dx
            entity.world_y += dy
            total_dx += dx
            total_dy += dy
            step_remaining -= dist
            entity.path.pop(0)
        else:
            # partial step toward the waypoint.
            nx = dx / dist
            ny = dy / dist
            entity.world_x += nx * step_remaining
            entity.world_y += ny * step_remaining
            total_dx += nx * step_remaining
            total_dy += ny * step_remaining
            step_remaining = 0
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


def clamp_player_to_bounds(world) -> None:
    # keep the player's *hitbox* inside the map rectangle. clamping the full
    # 128x128 sprite frame would stop the player ~40px before the visible
    # body actually reaches the edge, since most of the frame is transparent
    # padding. hitbox math matches Entity.hitbox_rect().
    player = world.get_player()
    sprite_w, sprite_h = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
    hitbox_w, hitbox_h = player.prototype.hitbox or (sprite_w, sprite_h)
    hx_off = (sprite_w - hitbox_w) / 2
    hy_off = sprite_h - hitbox_h
    map_w = world.width * TILE_LENGTH
    map_h = world.height * TILE_LENGTH
    player.world_x = max(-hx_off, min(player.world_x, map_w - hx_off - hitbox_w))
    player.world_y = max(-hy_off, min(player.world_y, map_h - hy_off - hitbox_h))
