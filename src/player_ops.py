
# player lifecycle helpers shared by single-player (game.Game) and the
# authoritative server (server.GameServer): spilling a dead/leaving player's
# goods onto the ground and respawning them. kept in one place so the SP and
# MP death paths can't drift — a known parity hazard.

from config import TILE_LENGTH


def spill_inventory_at_feet(world, player) -> None:
    # drop every occupied inventory slot as a ground item at the player's
    # visual center. does NOT clear the slots: callers that empty the inventory
    # (respawn) do so explicitly, while the disconnect path leaves them intact.
    at = player.center
    for slot in player.inventory.slots:
        if slot is not None:
            world.spawn_dropped_item(slot['item_id'], slot['quantity'], at)


def recenter_at_full_health(world, player) -> None:
    # place the player at the middle of the map at full health (respawn).
    sw, sh = player.sprite_dims
    player.world_x = world.width * TILE_LENGTH / 2 - sw / 2
    player.world_y = world.height * TILE_LENGTH / 2 - sh / 2
    player.health = player.max_health
