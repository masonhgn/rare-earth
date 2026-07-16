
# player lifecycle helpers shared by single-player (game.Game) and the
# authoritative server (server.GameServer): spilling a dead/leaving player's
# goods onto the ground and respawning them. kept in one place so the SP and
# MP death paths can't drift — a known parity hazard.

from config import TILE_LENGTH
import skills


def apply_health_level(player, heal_delta: bool = False) -> None:
    # recompute the player's max hp from the Health skill level (skills.py is the
    # single source of truth for the number). heal_delta=True — used on a Health
    # level-up — also grants the newly-added hp so leveling reads as a heal;
    # otherwise current hp is just clamped to the (possibly new) max. no-op for a
    # player with no skills component (e.g. an accountless server player).
    if player.skills is None:
        return
    old_max = player.max_health
    new_max = skills.max_hp_for(skills.level_of(player.skills, 'health'))
    player.max_health = new_max
    if heal_delta and new_max > old_max:
        player.health = min(new_max, (player.health or 0) + (new_max - old_max))
    elif player.health is not None:
        player.health = min(player.health, new_max)


def grant_xp(world, player, skill: str, amount: float) -> list:
    # grant `amount` xp to one of `player`'s skill tracks, apply any stat effects
    # of the levels crossed (Health -> higher max hp, as a heal), and queue the
    # level-ups on the world for the HUD toast. returns the (skill, level) list.
    # safe on a player with no skills (accountless server player) -> returns [].
    if player is None or player.skills is None:
        return []
    ups = skills.grant(player.skills, skill, amount)
    if not ups:
        return ups
    if any(track == 'health' for track, _ in ups):
        apply_health_level(player, heal_delta=True)
    world.pending_level_ups.extend(ups)
    # safety cap: the client drains this every frame, but a headless server never
    # does, so bound it here to keep a long-lived server from leaking.
    if len(world.pending_level_ups) > 64:
        del world.pending_level_ups[:-64]
    return ups


def spill_inventory_at_feet(world, player) -> None:
    # drop every occupied inventory slot as a ground item at the player's
    # visual center. does NOT clear the slots: callers that empty the inventory
    # (respawn) do so explicitly, while the disconnect path leaves them intact.
    at = player.center
    for slot in player.inventory.slots:
        if slot is not None:
            world.spawn_dropped_item(slot['item_id'], slot['quantity'], at)


def recenter_at_full_health(world, player) -> None:
    # place the player at the middle of the map at full health (respawn). the
    # leveled max is reapplied first so respawn honors the Health skill.
    sw, sh = player.sprite_dims
    player.world_x = world.width * TILE_LENGTH / 2 - sw / 2
    player.world_y = world.height * TILE_LENGTH / 2 - sh / 2
    apply_health_level(player)
    player.health = player.max_health
