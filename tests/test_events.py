
# one-shot presentation events — the channel that carries attack swings + hits
# from the authoritative sim to every renderer (single-player + net client).
# these guard the parity contract: an effect emitted by the sim must survive
# serialization and dispatch the same way on both sides.

import pygame as pg

import effects
from simcore import SimCore
from combat import CombatSystem
from breaking import BreakSystem
from entity import Entity
from prototype import load_prototype
import movement
import netproto
import json


def _sim_with_goblin():
    pg.init()
    sim = SimCore(seed_default=False)
    sim.seed()
    player = sim.world.get_player()
    gob = Entity(load_prototype('goblin'), (player.world_x + 40, player.world_y))
    sim.world.add_entity(gob)
    return sim, player, gob


def test_combat_hit_emits_a_hit_event_instead_of_spawning_directly():
    sim, player, gob = _sim_with_goblin()
    sim.world.events.clear()
    sim.combat.hit(player, gob, pg.time.get_ticks())
    # the number is presentation now, so no direct spawn — it rides an event.
    assert sim.combat.damage_numbers == []
    hits = [e for e in sim.world.events if e['kind'] == 'hit']
    assert len(hits) == 1 and 'amount' in hits[0]


def test_events_serialize_onto_the_wire_and_dispatch_locally():
    sim, player, gob = _sim_with_goblin()
    sim.world.events.clear()
    sim.world.emit('attack', id=player.id, facing='right')
    sim.combat.hit(player, gob, pg.time.get_ticks())

    # round-trip through the snapshot codec, as the server -> client path does.
    decoded = json.loads(netproto.encode({'events': sim.world.events})[4:].decode())

    combat = CombatSystem(sim.world)
    bs = BreakSystem(sim.world)
    now = pg.time.get_ticks()
    for e in decoded['events']:
        effects.apply(sim.world, bs, combat, e, now)

    assert player.anim.oneshot and 'attacking' in player.anim.current_state
    assert len(combat.damage_numbers) == 1


def test_movement_update_does_not_clobber_a_swing_mid_play():
    sim, player, gob = _sim_with_goblin()
    effects.apply(sim.world, BreakSystem(sim.world), None,
                  {'kind': 'attack', 'id': player.id, 'facing': 'left'}, pg.time.get_ticks())
    assert player.anim.oneshot
    # a walk vector while the swing plays must be ignored until it finishes.
    movement.update_player_animation(player, 6.0, 0.0)
    assert player.anim.oneshot and 'attacking' in player.anim.current_state


def test_unknown_event_kind_is_ignored():
    sim, player, gob = _sim_with_goblin()
    # a newer server may add kinds an older client doesn't know — must no-op.
    effects.apply(sim.world, BreakSystem(sim.world), None,
                  {'kind': 'not_a_real_kind'}, pg.time.get_ticks())
