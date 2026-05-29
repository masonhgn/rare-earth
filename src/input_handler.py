
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
            elif event.button == 3:
                _on_right_click(game, event)

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
        if game.factory_panel.open:
            game.close_factory_panel()
        else:
            game.running = False
    elif key == pg.K_q and (mods & pg.KMOD_CTRL):
        game.running = False


def _on_left_click(game, event) -> None:
    # priority order:
    #   1. clicks inside the open factory panel -> factory slot interaction
    #   2. clicks inside the open inventory -> slot interaction
    #   3. mouse currently dragging an item -> drop into world
    #   4. otherwise -> start a break at the cursor tile
    mx, my = event.pos

    if game.factory_panel.open and game.factory_panel.slot_at_pixel((mx, my)) is not None:
        # only intercept when the click is on an actual slot — clicks on the
        # panel background (decorative areas, gaps between slots) fall through
        # to world handling so they close the panel and walk normally.
        new_held = game.factory_panel.handle_click((mx, my), _held_payload(game.held_item))
        game.held_item = (
            None if new_held is None
            else {**new_held, 'screen_pos': (mx - _HELD_HALF, my - _HELD_HALF)}
        )
        return

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

    # is this a machine (factory) click? — drives the open/close routing.
    clicked_machine = None
    entity_here = game.world.get_entity_at_tile(*tile)
    if entity_here is not None and entity_here.machine_state is not None:
        clicked_machine = entity_here

    # toggle: clicking on the machine whose panel is already open closes it.
    if (clicked_machine is not None and game.factory_panel.open
            and game.factory_panel.entity is clicked_machine):
        game.close_factory_panel()
        return

    # any other world click while the panel is open closes the panel before
    # processing the click — modal close-on-click outside.
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
    if clicked_machine is not None and any(
            game.world.tile_in_reach(tx, ty, max_dist=1)
            for tx, ty in clicked_machine.footprint()):
        game.factory_panel.open_for(clicked_machine, (game.screen.width, game.screen.height))
        game.inventory.open = True
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
    # remember the pending action so Game._update fires it on arrival.
    game.pending_break = breakable      # may be None
    game.pending_open = clicked_machine  # may be None


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


def _on_right_click(game, event) -> None:
    # right-click is currently a no-op. (rock placement was removed in the
    # click-to-walk pass.)
    return


def _held_payload(held: dict | None) -> dict | None:
    # strip transient ui fields (screen_pos) so the panel only sees what
    # it actually cares about — item_id + quantity.
    if held is None:
        return None
    return {'item_id': held['item_id'], 'quantity': held['quantity']}


