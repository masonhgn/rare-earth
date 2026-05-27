
# input dispatch.
#
# the loop in game.py calls event_loop() each frame for discrete events
# (key-presses, mouse clicks), and poll_movement() for held movement keys.
# break/drag-mining state lives in Game.break_system; this module just
# routes events into it.

import pygame as pg

from config import TILE_LENGTH, ITEM_ICON_SIZE
from entity import Entity
from prototype import load_prototype
from world import world_to_tile


# offset for centering the held item icon on the cursor
_HELD_HALF = ITEM_ICON_SIZE // 2


def poll_movement(player, dt: float) -> tuple[float, float]:
    # returns the (dx, dy) the player would move this frame in pixels.
    # the player's animation state is updated here since it is purely
    # input-derived.
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
    dx = vx * speed * dt
    dy = vy * speed * dt

    # animation state from dominant axis. pure vertical keeps the last
    # horizontal facing instead of flipping to idle mid-walk.
    if player.anim is not None:
        if vx > 0:
            player.anim.set_state('walking_right')
        elif vx < 0:
            player.anim.set_state('walking_left')
        elif vy == 0:
            player.anim.set_state('idle')
        elif player.anim.current_state not in ('walking_left', 'walking_right'):
            player.anim.set_state('idle')

    return dx, dy


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

        elif event.type == pg.MOUSEBUTTONUP:
            if event.button == 1:
                # release ends drag-mining and cancels any active break.
                game.break_system.cancel()

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
        game.running = False
    elif key == pg.K_q and (mods & pg.KMOD_CTRL):
        game.running = False


def _on_left_click(game, event) -> None:
    # priority order:
    #   1. clicks inside the open inventory -> slot interaction
    #   2. mouse currently dragging an item -> drop into world
    #   3. otherwise -> start a break at the cursor tile
    mx, my = event.pos

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

    wx, wy = game.screen.camera.screen_to_world((mx, my))
    tile = world_to_tile((wx, wy))
    target = game.break_system.try_acquire_target(tile)
    if target is None:
        return
    proto, entity_id = target
    game.break_system.start_break(proto, tile, entity_id=entity_id)


def _on_right_click(game, event) -> None:
    # place a rock at the cursor tile, consuming one rock_chunk from inventory.
    # extend later for general item->entity placement via a recipe table.
    mx, my = event.pos
    if game.inventory.open and game.inventory.rect.collidepoint((mx, my)):
        return
    wx, wy = game.screen.camera.screen_to_world((mx, my))
    tx, ty = world_to_tile((wx, wy))

    if not game.world.tile_in_reach(tx, ty):
        return
    if not game.world.in_bounds_tile(tx, ty):
        return
    if game.world.get_entity_at_tile(tx, ty) is not None:
        return

    item_id, proto_name = 'rock_chunk', 'rock'
    if not _consume_inventory(game.inventory, item_id, 1):
        return

    entity = Entity(load_prototype(proto_name), (tx * TILE_LENGTH, ty * TILE_LENGTH))
    try:
        game.world.add_entity(entity)
    except ValueError:
        # tile occupied between our check and add (multi-tile overlap); refund
        game.inventory.add_item(item_id, 1)


def _consume_inventory(inventory, item_id: str, quantity: int) -> bool:
    # remove `quantity` of item_id from inventory if available; else no-op.
    # returns whether the consumption happened.
    total = sum(s['quantity'] for s in inventory.slots if s and s['item_id'] == item_id)
    if total < quantity:
        return False
    remaining = quantity
    for i, slot in enumerate(inventory.slots):
        if remaining <= 0:
            break
        if slot is None or slot['item_id'] != item_id:
            continue
        take = min(slot['quantity'], remaining)
        slot['quantity'] -= take
        remaining -= take
        if slot['quantity'] <= 0:
            inventory.slots[i] = None
    return True
