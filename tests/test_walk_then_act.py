
# walk-then-act: clicking a target you're not in range of makes the host walk
# you there and fire on arrival (mine / swing). authoritative + shared by both
# transports, so single-player regains its walk-to-mine/attack and multiplayer
# gains it. these drive WorldHost directly in a cleared arena so pathfinding +
# arrival are deterministic.

import pygame as pg

from config import TILE_LENGTH
from simcore import SimCore
from world_host import WorldHost
from entity import Entity
from prototype import load_prototype
import interaction


def _arena(clear_radius=10):
    # a host with one player standing in a cleared grass arena (no mobs, no rock
    # in the way), so a queued pursuit can path + arrive without obstruction.
    pg.init()
    sim = SimCore(seed_default=False)
    sim.world.remove_entity('player')   # drop the default player (server does this too)
    host = WorldHost(sim)
    conn = host.create_player()
    host.register('p', conn)
    p = sim.world.entities[conn.player_id]
    tx, ty = p.center_tile
    for cx in range(tx - clear_radius, tx + clear_radius):
        for cy in range(ty - clear_radius, ty + clear_radius):
            if 0 <= cy < sim.world.height and 0 <= cx < sim.world.width:
                sim.world.map_grid[cy][cx] = 'grass'
                sim.world.overlay_grid[cy][cx] = None
    return sim, host, conn, p, (tx, ty)


def test_walk_then_break_walks_over_and_mines():
    sim, host, conn, p, (tx, ty) = _arena()
    ore_tile = (tx + 5, ty)
    sim.world.overlay_grid[ore_tile[1]][ore_tile[0]] = 'coal_ore'
    assert interaction.can_mine(p, load_prototype('coal_ore')), 'coal must be minable at level 1'

    host.apply_intent(conn, {'type': 'break', 'tile': [ore_tile[0], ore_tile[1]]})
    assert conn.pending_action == ('break', ore_tile)

    mined = False
    for _ in range(200):   # ~10 sim seconds, plenty to walk 5 tiles + mine
        host.tick(1 / 20)
        if sim.world.overlay_at(*ore_tile) is None:
            mined = True
            break
    assert mined, 'player never walked over and mined the out-of-reach ore'
    assert conn.pending_action is None, 'pursuit not cleared after mining'


def test_walk_then_attack_walks_over_and_hits():
    sim, host, conn, p, (tx, ty) = _arena()
    gob = Entity(load_prototype('goblin'), ((tx + 5) * TILE_LENGTH, ty * TILE_LENGTH))
    sim.world.add_entity(gob)
    hp0 = gob.health

    host.apply_intent(conn, {'type': 'attack', 'target': gob.id})
    assert conn.pending_action == ('attack', gob.id)

    hit = False
    for _ in range(200):
        host.tick(1 / 20)
        if gob.id not in sim.world.entities or (gob.health is not None and gob.health < hp0):
            hit = True
            break
    assert hit, 'player never reached and hit the out-of-range goblin'


def test_wasd_preempts_a_queued_pursuit():
    sim, host, conn, p, (tx, ty) = _arena()
    ore_tile = (tx + 5, ty)
    sim.world.overlay_grid[ore_tile[1]][ore_tile[0]] = 'coal_ore'
    host.apply_intent(conn, {'type': 'break', 'tile': [ore_tile[0], ore_tile[1]]})
    assert conn.pending_action is not None
    # a held movement key must cancel the queued action.
    host.apply_intent(conn, {'type': 'move', 'dx': -1, 'dy': 0})
    host.tick(1 / 20)
    assert conn.pending_action is None, 'WASD did not preempt the pursuit'
    assert sim.world.overlay_at(*ore_tile) == 'coal_ore', 'ore was mined despite preempt'
