
# shared interaction detection + validation.
#
# pure helpers used by every path — single-player input (input_handler), the
# break system, the net client, and the authoritative server — so "what's
# breakable / placeable / openable here?" and "is this mob under the cursor?"
# live in ONE place instead of drifting across copies.
#
# everything takes `world` (and, where reach matters, an explicit `player`) as
# a parameter and never calls world.get_player(): the fixed 'player' id only
# exists in single-player; the server uses per-connection players and the
# client uses its local_id player.

from item import load_item
from prototype import load_prototype
from world import in_reach


def breakable_at(world, tile):
    # (prototype, entity_id_or_None) for whatever is breakable at `tile`, or
    # None. a placed editable entity wins over an overlay ore tile. NO reach
    # check — callers that want one apply it themselves (single-player
    # click-to-walk deliberately plans toward out-of-reach targets).
    tx, ty = tile
    entity = world.get_entity_at_tile(tx, ty)
    if entity is not None and entity.prototype.editable:
        return (entity.prototype, entity.id)
    overlay_id = world.overlay_at(tx, ty)
    if overlay_id is None:
        return None
    try:
        proto = load_prototype(overlay_id)
    except FileNotFoundError:
        return None
    return (proto, None) if proto.editable else None


def mob_at(world, wx: float, wy: float, exclude_id=None):
    # the mob whose visible body (hitbox) contains the world point, or None.
    for ent in world.entities_with('mob'):
        if ent.id == exclude_id:
            continue
        if ent.hitbox_rect().collidepoint(wx, wy):
            return ent
    return None


def openable_at(world, tile, adjacency: int = 1):
    # the interactable entity to open for a click on `tile`: the entity at the
    # tile if it's interactable, else an interactable entity with a footprint
    # tile within `adjacency` (Chebyshev) of the click — so clicking grass next
    # to a building opens the building. NO reach/proximity gate (callers add
    # their own).
    tx, ty = tile
    entity = world.get_entity_at_tile(tx, ty)
    if entity is not None and entity.prototype.interactable is not None:
        return entity
    for ent in world.entities.values():
        if ent.prototype.interactable is None:
            continue
        if any(max(abs(tx - ftx), abs(ty - fty)) <= adjacency
               for ftx, fty in ent.footprint()):
            return ent
    return None


def can_place(world, player, tile, held) -> bool:
    # can `player` place their `held` item on `tile`? True only when the held
    # item is placeable and the tile is a valid, in-reach, empty target: in
    # bounds, no overlay feature, no entity, walkable, within reach, and not
    # the tile under the player's own feet. INCLUDES the reach check.
    if held is None or load_item(held['item_id']).places is None:
        return False
    tx, ty = tile
    if not world.in_bounds_tile(tx, ty):
        return False
    if world.overlay_at(tx, ty) is not None:
        return False
    if world.get_entity_at_tile(tx, ty) is not None:
        return False
    if not world.is_walkable(tx, ty):
        return False
    if not in_reach(player, tx, ty):
        return False
    if (tx, ty) == player.center_tile:
        return False
    return True
