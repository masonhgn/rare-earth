
# shared interaction detection + validation.
#
# pure helpers used by every path — the client's input handling, the break
# system, and the authoritative host — so "what's breakable / placeable /
# openable here?" and "is this mob under the cursor?" live in ONE place instead
# of drifting across copies.
#
# everything takes `world` (and, where reach matters, an explicit `player`) as
# a parameter and never calls world.get_player(): the fixed 'player' id only
# exists in single-player; the server uses per-connection players and the
# client uses its local_id player.

from item import load_item
from prototype import load_prototype
from world import in_reach
import skills


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


def can_mine(player, proto) -> bool:
    # Mining-level gate: True unless breaking `proto` needs a higher Mining level
    # than `player` has. shared by SP (BreakSystem), the client's break preflight,
    # and the authoritative server so all three agree on what's mineable.
    req = getattr(proto, 'mining_level', 1) or 1
    if req <= 1:
        return True
    return (player is not None and player.skills is not None
            and skills.level_of(player.skills, 'mining') >= req)


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
    if held is None:
        return False
    item = load_item(held['item_id'])
    if item.places is None:
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
    # terrain-restricted placeables (crops) only go on their required base
    # tile — e.g. wheat only on grass. other placeables have plant_on=None.
    placed = load_prototype(item.places)
    if placed.plant_on is not None and world.base_at(tx, ty) != placed.plant_on:
        return False
    # Farming-level gate: crops with farming_level > 1 need the skill to plant.
    # non-crop placeables default to 1 (no gate). shows as a red build highlight.
    if placed.farming_level > 1:
        if player.skills is None or skills.level_of(player.skills, 'farming') < placed.farming_level:
            return False
    return True
