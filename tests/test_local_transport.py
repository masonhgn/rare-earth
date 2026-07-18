
# single-player as a listen server: LocalTransport drives the exact same Client
# as multiplayer, over an in-process WorldHost/SimCore instead of a socket. these
# cover the three things that make the cutover safe: the host applies intents
# authoritatively, a full Client session runs headless, and the save format
# round-trips through LocalTransport's duck-typed adapter.

import os
import tempfile

import pygame as pg

from simcore import SimCore
from world_host import WorldHost
from world import world_to_tile
from transport import LocalTransport
from client import Client


def _tmp_save():
    path = os.path.join(tempfile.gettempdir(), '_reearth_localtx_test.json')
    try:
        os.remove(path)
    except OSError:
        pass
    return path


def test_world_host_applies_a_move_intent_authoritatively():
    # the host is the authority: a validated move intent moves the player on the
    # next tick. (the Client re-derives movement from live keys, so this is where
    # movement is actually exercised.) unseeded so no mob separation interferes,
    # with a cleared lane so terrain can't block the step.
    pg.init()
    sim = SimCore(seed_default=False)   # no seed -> no mobs to shove the player
    sim.world.remove_entity('player')   # drop the default player (server does this too)
    host = WorldHost(sim)
    conn = host.create_player()
    host.register('p', conn)
    p = sim.world.entities[conn.player_id]
    tx, ty = world_to_tile(p.center)
    for cx in range(tx - 1, tx + 6):
        for cy in range(ty - 2, ty + 3):
            sim.world.map_grid[cy][cx] = 'grass'
            sim.world.overlay_grid[cy][cx] = None
    x0 = p.world_x
    host.apply_intent(conn, {'type': 'move', 'dx': 1, 'dy': 0})
    for _ in range(5):
        host.tick(1 / 20)
    assert p.world_x > x0, 'player did not move right under a held move intent'


def test_local_client_session_runs_headless():
    # a fresh single-player session: build + pump real frames (render included,
    # via the SDL dummy driver) and confirm the loop stays alive, the local
    # player syncs into the client mirror, and sim time advances.
    path = _tmp_save()
    lt = LocalTransport(save_path=path, world_name='test')
    c = Client(lt, lt.connect())
    try:
        # single-player gets a dev console; it's driven by the transport's hook.
        assert c.dev_console is not None, 'single-player Client should have a dev console'
        for _ in range(30):
            assert c.step_frame(), 'client stopped unexpectedly'
        assert c.world.entities.get(c.local_id) is not None, 'local player never synced'
        assert lt.sim.day_clock.elapsed > 0.0, 'sim clock did not advance'
    finally:
        lt.close()
    assert os.path.exists(path), 'session did not save on close'
    os.remove(path)


def test_dev_commands_mutate_the_sim():
    # the console's command table mutates the authoritative sim directly (the
    # change then rides the snapshot to the client mirror in a real session).
    path = _tmp_save()
    lt = LocalTransport(save_path=path)
    lt.connect()
    cmds = lt.admin_commands()
    p = lt.sim.world.get_player()
    try:
        assert '3x wheat' in cmds['give'][0](['wheat', '3'])
        assert any(s and s['item_id'] == 'wheat' for s in p.inventory.slots)
        assert 'unknown item' in cmds['give'][0](['not_a_real_item'])

        cmds['sethp'][0](['1'])
        assert p.health == 1
        cmds['heal'][0]([])
        assert p.health == p.max_health

        cmds['day'][0](['5'])
        assert lt.sim.day_clock.day == 5

        cmds['tp'][0](['10', '12'])
        assert p.center_tile == (10, 12)
    finally:
        lt.close()
        os.remove(path)


def test_networked_transport_has_no_admin_hook():
    # SocketTransport must NOT expose admin_commands — a networked client can't
    # mutate the authoritative world, so it gets no console.
    from transport import SocketTransport
    assert not hasattr(SocketTransport, 'admin_commands')


def _client_with_building():
    # a single-player Client whose mirror has synced the seeded world; returns it
    # plus an openable building and the local player mirror.
    path = _tmp_save()
    lt = LocalTransport(save_path=path)
    c = Client(lt, lt.connect())
    for _ in range(3):
        c.step_frame()   # let the mirror sync entities from snapshots
    building = next(e for e in c.world.entities.values()
                    if e.prototype.interactable is not None)
    return path, lt, c, building, c.world.entities[c.local_id]


def test_walk_to_open_opens_the_panel_on_arrival():
    from config import TILE_LENGTH
    path, lt, c, building, lp = _client_with_building()
    try:
        # stand the player mirror on a footprint tile (adjacent), queue the open,
        # and resolve it — as _step does each frame once the host walks us there.
        ftx, fty = building.footprint()[0]
        lp.world_x, lp.world_y = ftx * TILE_LENGTH, fty * TILE_LENGTH
        assert c._adjacent_to(lp, building)
        c.pending_open = building.id
        c._update_pending_open((0, 0))
        assert (c.factory_panel.open or c.exchange_panel.open), 'panel did not open on arrival'
        assert c.pending_open is None, 'pending open not cleared after opening'
    finally:
        lt.close()
        os.remove(path)


def test_walk_to_open_cancels_on_manual_move():
    path, lt, c, building, lp = _client_with_building()
    try:
        c.pending_open = building.id
        c._update_pending_open((1, 0))   # a held direction cancels the queued open
        assert c.pending_open is None
        assert not c.factory_panel.open and not c.exchange_panel.open
    finally:
        lt.close()
        os.remove(path)


def test_local_save_round_trips_player_state():
    # mutate the authoritative player, close (which saves via the sp format), then
    # reconnect a new LocalTransport on the same slot and confirm position +
    # inventory were restored — proving the duck-typed save adapter works.
    path = _tmp_save()
    lt = LocalTransport(save_path=path, world_name='roundtrip')
    lt.connect()
    p = lt.sim.world.get_player()
    p.world_x, p.world_y = 512.0, 640.0
    p.inventory.slots[0] = {'item_id': 'wheat', 'quantity': 7}
    lt.close()

    lt2 = LocalTransport(save_path=path)
    lt2.connect()
    p2 = lt2.sim.world.get_player()
    try:
        assert (p2.world_x, p2.world_y) == (512.0, 640.0), 'position not restored'
        assert p2.inventory.slots[0] == {'item_id': 'wheat', 'quantity': 7}, 'inventory not restored'
        assert lt2.world_name == 'roundtrip', 'world name not restored'
    finally:
        lt2.close()
        os.remove(path)
