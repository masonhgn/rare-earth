
# input dispatch.
#
# the loop in game.py calls event_loop() each frame for discrete events
# (key-presses, mouse clicks), and poll_movement() for held movement keys.
# break/drag-mining state lives in Game.break_system; this module just
# routes events into it.

import pygame as pg

from config import TILE_LENGTH, ITEM_ICON_SIZE
from pathfinding import find_path
from world import world_to_tile


# offset for centering the held item icon on the cursor
_HELD_HALF = ITEM_ICON_SIZE // 2


# --- pending click-to-walk-then-fire actions ---
#
# both "click on a breakable while too far away" and "click on a building
# while too far away" use the same pattern: plan a path, walk it, fire the
# action when the player arrives within reach. only one pending action can be
# queued at a time. when a click target is both openable AND breakable, the
# opener wins (matches the immediate-action priority below).

class PendingAction:
    # subclasses implement ready() and fire(). ready() reads game state
    # to decide if it's time to trigger; fire() performs the action.
    def ready(self, game) -> bool:
        raise NotImplementedError

    def fire(self, game) -> None:
        raise NotImplementedError


class BreakOnArrival(PendingAction):
    # validates the target still exists before starting the break so a
    # drag-mining pass that already broke it doesn't trigger a phantom hit.
    def __init__(self, proto, entity_id, tile) -> None:
        self.proto = proto
        self.entity_id = entity_id
        self.tile = tile

    def ready(self, game) -> bool:
        return game.world.tile_in_reach(*self.tile)

    def fire(self, game) -> None:
        if self._still_there(game):
            game.break_system.start_break(
                self.proto, self.tile, entity_id=self.entity_id,
            )

    def _still_there(self, game) -> bool:
        if self.entity_id is None:
            return game.world.overlay_at(*self.tile) is not None
        return self.entity_id in game.world.entities


class OpenOnArrival(PendingAction):
    # ready when any footprint tile of the target is within adjacency
    # distance. dispatch to the right panel happens via the registered
    # opener for the prototype's interactable kind.
    def __init__(self, target) -> None:
        self.target = target

    def ready(self, game) -> bool:
        return any(
            game.world.tile_in_reach(tx, ty, max_dist=1)
            for tx, ty in self.target.footprint()
        )

    def fire(self, game) -> None:
        game.open_modal_for_entity(self.target)


def poll_movement(player, dt: float) -> tuple[float, float]:
    # returns the (dx, dy) the player would move this frame in pixels.
    # animation state is set by Game._update_player_animation after movement
    # is resolved — keeping it here would double-update and clobber the
    # path-follower's facing during pure-vertical segments.
    keys = pg.key.get_pressed()
    vx = vy = 0.0
    if keys[pg.K_w]:
        vy -= 1
    if keys[pg.K_s]:
        vy += 1
    if keys[pg.K_a]:
        vx -= 1
    if keys[pg.K_d]:
        vx += 1

    # diagonal normalization so 45deg movement isn't sqrt(2) faster
    if vx != 0 and vy != 0:
        inv = 0.7071067811865475
        vx *= inv
        vy *= inv

    speed = player.prototype.speed or 0.0
    return vx * speed * dt, vy * speed * dt


def event_loop(game) -> None:
    for event in pg.event.get():
        if event.type == pg.QUIT:
            game.running = False

        elif event.type == pg.KEYDOWN:
            _on_keydown(game, event)

        elif event.type == pg.MOUSEBUTTONDOWN:
            if event.button == 1:
                _on_left_click(game, event)

        elif event.type == pg.MOUSEWHEEL:
            # wheel scrolls the active exchange tab content (spot list,
            # contracts list, drop-box slots) when the panel is open.
            if game.exchange_panel.open:
                game.exchange_panel.handle_scroll(pg.mouse.get_pos(), event.y)

        elif event.type == pg.MOUSEMOTION:
            if game.held_item is not None:
                mx, my = event.pos
                game.held_item['screen_pos'] = (mx - _HELD_HALF, my - _HELD_HALF)
            game.hover_pos = event.pos


def _on_keydown(game, event) -> None:
    key = event.key
    mods = pg.key.get_mods()

    if key == pg.K_b:
        game.inventory.toggle()
    elif key == pg.K_F2:
        game.toggle_fullscreen()
    elif key == pg.K_F3:
        game.hud.toggle()
    elif key == pg.K_ESCAPE:
        # close the highest-priority open panel first; if none are open,
        # bring up the settings modal (manual save + display mode).
        if game.settings_panel.open:
            game.settings_panel.close()
        elif game.exchange_panel.open:
            game.close_exchange_panel()
        elif game.factory_panel.open:
            game.close_factory_panel()
        else:
            game.settings_panel.open_panel((game.screen.width, game.screen.height))
    elif key == pg.K_q and (mods & pg.KMOD_CTRL):
        game.running = False


def _on_left_click(game, event) -> None:
    # cascade (top to bottom):
    #   1. always-on hud tabs
    #   2. open modals (settings, exchange, factory) — each implements
    #      the (open / hit / handle_click) shape. modal.hit() defines
    #      what counts as "inside the interactive area"; the exact
    #      definition differs per modal (exchange = panel rect, factory
    #      = on a slot, settings = panel rect).
    #   3. inventory grid clicks
    #   4. drop held item into world
    #   5. world click for walk/break/open
    mx, my = event.pos

    # hud tabs sit on top and take priority over modal-outside-dismiss
    # so clicking the settings tab while settings is open toggles
    # cleanly rather than first dismissing as an outside-click.
    if game.hud_tabs.handle_click((mx, my)):
        return

    for modal, on_outside in _modal_cascade(game):
        if not modal.open:
            continue
        if modal.hit((mx, my)):
            _route_modal_click(game, modal, (mx, my))
            return
        on_outside()

    if game.inventory.open:
        slot = game.inventory.slot_at_pixel((mx, my))
        if slot is not None:
            # handle_click reads only {item_id, quantity}; held_item has those
            # plus screen_pos. extra keys are ignored, so pass it directly.
            new_slot = game.inventory.handle_click(slot, game.held_item)
            game.held_item = (
                None if new_slot is None
                else {**new_slot, 'screen_pos': (mx - _HELD_HALF, my - _HELD_HALF)}
            )
            return
        # clicked on the inventory panel but on a border: do nothing
        if game.inventory.rect.collidepoint((mx, my)):
            return

    if game.held_item is not None:
        wx, wy = game.screen.camera.screen_to_world((mx, my))
        game.world.spawn_dropped_item(
            game.held_item['item_id'], game.held_item['quantity'], (wx, wy),
        )
        game.held_item = None
        return

    # click-to-walk: pathfind to the clicked tile. drop a click-marker at
    # the exact world position so the "X" lands where the player aimed.
    wx, wy = game.screen.camera.screen_to_world((mx, my))
    tile = world_to_tile((wx, wy))
    if not game.world.in_bounds_tile(*tile):
        return

    # what kind of entity is at the click? — drives the open/close routing.
    # both machines and the exchange use the same pending_open mechanism,
    # so we just classify and let the open-or-toggle logic below act on it.
    #
    # also catches clicks on walkable tiles immediately adjacent to an
    # openable's footprint, since the player usually means to interact
    # with the building when they click on grass right next to it.
    # without this, clicking a tile one step off the exchange walked
    # them there but never triggered the open.
    clicked_openable = None
    entity_here = game.world.get_entity_at_tile(*tile)
    if entity_here is not None and entity_here.prototype.interactable is not None:
        clicked_openable = entity_here
    else:
        for ent in game.world.entities.values():
            if ent.prototype.interactable is None:
                continue
            if any(max(abs(tile[0] - tx), abs(tile[1] - ty)) <= 1
                   for tx, ty in ent.footprint()):
                clicked_openable = ent
                break

    # toggle: clicking on whichever entity's panel is already open closes it.
    if clicked_openable is not None:
        if (game.factory_panel.open and game.factory_panel.entity is clicked_openable):
            game.close_factory_panel()
            return
        if (game.exchange_panel.open and game.exchange_panel.entity is clicked_openable):
            game.close_exchange_panel()
            return

    # any other world click while a panel is open closes it before
    # processing — modal close-on-click outside.
    if game.factory_panel.open:
        game.close_factory_panel()

    # if the click hits a solid (e.g. the factory body), reroute to the
    # nearest walkable cell so the player approaches it instead of stalling.
    walk_target = tile if game.world.is_walkable(*tile) else game.world.nearest_walkable(*tile)
    if walk_target is None:
        return

    # detect a breakable at the click
    breakable = _click_breakable_at(game, tile)

    # immediate-action shortcuts: if the click target is already in reach,
    # fire the action and skip the path.
    if clicked_openable is not None and any(
            game.world.tile_in_reach(tx, ty, max_dist=1)
            for tx, ty in clicked_openable.footprint()):
        game.open_modal_for_entity(clicked_openable)
        game.click_marker = ((wx, wy), pg.time.get_ticks())
        return
    if breakable is not None and game.world.tile_in_reach(*breakable[2]):
        proto, entity_id, t = breakable
        game.break_system.start_break(proto, t, entity_id=entity_id)
        game.click_marker = ((wx, wy), pg.time.get_ticks())
        return

    # plan the path
    player = game.world.get_player()
    sprite_w, sprite_h = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
    player_tile = world_to_tile((player.world_x + sprite_w / 2, player.world_y + sprite_h / 2))
    path = find_path(game.world, player_tile, walk_target)
    if path is None:
        return
    player.path = path
    game.click_marker = ((wx, wy), pg.time.get_ticks())
    # queue a single arrival action. opener wins over breakable if both
    # were detected — matches the immediate-action priority in this same
    # handler when the player is already in range.
    if clicked_openable is not None:
        game.pending_action = OpenOnArrival(clicked_openable)
    elif breakable is not None:
        proto, entity_id, t = breakable
        game.pending_action = BreakOnArrival(proto, entity_id, t)
    else:
        game.pending_action = None


def _click_breakable_at(game, tile):
    # returns (prototype, entity_id_or_None, tile) for a breakable at `tile`,
    # or None. mirrors BreakSystem.try_acquire_target minus the reach check —
    # click-to-walk wants to *plan toward* out-of-reach targets, not reject.
    entity = game.world.get_entity_at_tile(*tile)
    if entity is not None and entity.prototype.editable:
        return (entity.prototype, entity.id, tile)
    overlay_id = game.world.overlay_at(*tile)
    if overlay_id is None:
        return None
    from prototype import load_prototype
    try:
        proto = load_prototype(overlay_id)
    except FileNotFoundError:
        return None
    if not proto.editable:
        return None
    return (proto, None, tile)


def _modal_cascade(game):
    # ordered list of (modal, outside-click handler) tuples. order = top
    # of z-stack first. each `on_outside` is what happens when the click
    # missed that modal's interactive area — settings/exchange use it to
    # dismiss, factory uses it as a pass-through so world handling still
    # runs (this matches the long-standing per-modal close behaviour).
    return [
        (game.settings_panel, game.settings_panel.close),
        (game.exchange_panel, game.close_exchange_panel),
        (game.factory_panel, lambda: None),
    ]


def _route_modal_click(game, modal, pos) -> None:
    # for modals that don't deal with held items (settings), handle_click
    # takes no held arg. for the others we feed the cursor payload in
    # and read the new held back out.
    if modal is game.settings_panel:
        modal.handle_click(pos)
        return
    new_held = modal.handle_click(pos, _held_payload(game.held_item))
    game.held_item = (
        None if new_held is None
        else {**new_held, 'screen_pos': (pos[0] - _HELD_HALF, pos[1] - _HELD_HALF)}
    )


def _held_payload(held: dict | None) -> dict | None:
    # strip transient ui fields (screen_pos) so the panel only sees what
    # it actually cares about — item_id + quantity.
    if held is None:
        return None
    return {'item_id': held['item_id'], 'quantity': held['quantity']}


