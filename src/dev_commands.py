
# single-player developer console commands.
#
# each returns one line of feedback and mutates the local sim directly. single-
# player is a listen server, so "direct" is just the in-process host — the
# mutation rides the next snapshot out to the client mirror like anything else
# (a big teleport trips the client's snap-on-desync, so it lands instantly).
# the console UI itself is generic (dev_console.py); this supplies its command
# table. multiplayer has no console — a networked client can't mutate the
# authoritative world.

import os

from config import TILE_LENGTH, ITEMS_DIR
from entity import Entity
from item import load_item
from prototype import load_prototype


def make(sim) -> dict:
    # build the {name: (handler, usage)} table bound to this sim. camera follow
    # isn't done here (the client camera tracks the player each frame on its own).
    def player():
        return sim.world.get_player()

    def teleport(args):
        if len(args) != 2:
            return 'usage: tp <tileX> <tileY>'
        try:
            tx, ty = int(args[0]), int(args[1])
        except ValueError:
            return 'tp: coordinates must be integers (tile units)'
        if not sim.world.in_bounds_tile(tx, ty):
            return f'tp: ({tx}, {ty}) out of bounds (0..{sim.world.width - 1}, 0..{sim.world.height - 1})'
        p = player()
        sw, sh = p.sprite_dims
        # center the sprite on the target tile's center.
        p.world_x = tx * TILE_LENGTH + TILE_LENGTH / 2 - sw / 2
        p.world_y = ty * TILE_LENGTH + TILE_LENGTH / 2 - sh / 2
        p.path = []
        return f'teleported to tile ({tx}, {ty})'

    def give(args):
        if not args:
            return 'usage: give <item_id> [qty]'
        item_id = args[0]
        qty = 1
        if len(args) >= 2:
            try:
                qty = int(args[1])
            except ValueError:
                return 'give: qty must be an integer'
        if qty <= 0:
            return 'give: qty must be positive'
        try:
            load_item(item_id)   # raises if the item json is missing
        except FileNotFoundError:
            return f'give: unknown item "{item_id}"   (try "items")'
        leftover = player().inventory.add_item(item_id, qty)
        got = qty - leftover
        msg = f'gave {got}x {item_id}'
        if leftover:
            msg += f'   ({leftover} did not fit)'
        return msg

    def items(args):
        ids = sorted(f[:-5] for f in os.listdir(ITEMS_DIR) if f.endswith('.json'))
        return 'items: ' + ', '.join(ids)

    def spawn(args):
        if not args:
            return 'usage: spawn <entity_id> [n]'
        proto_id = args[0]
        n = 1
        if len(args) >= 2:
            try:
                n = int(args[1])
            except ValueError:
                return 'spawn: n must be an integer'
        if n <= 0:
            return 'spawn: n must be positive'
        try:
            proto = load_prototype(proto_id)
        except FileNotFoundError:
            return f'spawn: unknown entity "{proto_id}"'
        p = player()
        spawned = 0
        for i in range(n):
            # scatter around the player so multiple spawns don't stack on one tile.
            ox = (i % 3 - 1) * TILE_LENGTH
            oy = (i // 3) * TILE_LENGTH
            pos = (p.world_x + TILE_LENGTH + ox, p.world_y + oy)
            try:
                sim.world.add_entity(Entity(proto, pos))
                spawned += 1
            except ValueError:
                pass   # tile-locked footprint occupied; skip this one
        return f'spawned {spawned}x {proto_id}' + ('' if spawned == n else f'   ({n - spawned} blocked)')

    def heal(args):
        p = player()
        if p.max_health is None:
            return 'heal: player has no health'
        p.health = p.max_health
        return f'healed to {p.health}/{p.max_health}'

    def sethp(args):
        if len(args) != 1:
            return 'usage: sethp <n>'
        p = player()
        if p.max_health is None:
            return 'sethp: player has no health'
        try:
            hp = int(args[0])
        except ValueError:
            return 'sethp: n must be an integer'
        p.health = max(0, min(hp, p.max_health))
        return f'health = {p.health}/{p.max_health}'

    def day(args):
        if not args:
            return f'day {sim.day_clock.day}'
        try:
            n = int(args[0])
        except ValueError:
            return 'day: n must be an integer'
        if n < 1:
            return 'day: must be >= 1'
        sim.day_clock.set_day(n)
        return f'day set to {sim.day_clock.day}'

    commands: dict = {}

    def help_cmd(args):
        return 'commands: ' + ', '.join(sorted(commands))

    commands.update({
        'tp': (teleport, 'tp <tileX> <tileY>'),
        'teleport': (teleport, 'teleport <tileX> <tileY>'),
        'give': (give, 'give <item_id> [qty]'),
        'items': (items, 'items'),
        'spawn': (spawn, 'spawn <entity_id> [n]'),
        'heal': (heal, 'heal'),
        'sethp': (sethp, 'sethp <n>'),
        'day': (day, 'day [n]'),
        'help': (help_cmd, 'help'),
    })
    return commands
