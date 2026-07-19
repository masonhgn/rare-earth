
# regressions for the map-view crash + rock/ore world-gen rules.

import pygame as pg

import config
import world as world_mod
from world import World
from entity import Entity
from prototype import load_prototype


def _small_world(dim=120):
    # a freshly generated world at `dim` x `dim` (world.py reads the module dims
    # at construction). small keeps the ore/patch scan fast.
    world_mod.WORLD_WIDTH = dim
    world_mod.WORLD_HEIGHT = dim
    return World()


def test_map_view_renders_without_the_fixed_player_id():
    # the net client's world has no 'player' entity (its player is 'player_0'),
    # so MapView must take the local player explicitly instead of get_player().
    pg.init()
    from render import Screen, MapView

    w = _small_world()
    w.remove_entity('player')                                   # like the server does
    lp = Entity(load_prototype('player'), (400, 400), entity_id='player_0')
    w.add_entity(lp)

    screen = Screen(320, 240, display_mode='windowed')
    mv = MapView(w)
    mv.open = True
    # the crash was here (world.get_player() -> KeyError 'player').
    mv.render(screen.surface, (320, 240), screen.camera, lp)
    mv.render(screen.surface, (320, 240), screen.camera, None)   # also fine with no player


def test_rock_patches_stay_inside_the_world():
    w = _small_world()
    max_x, max_y = w.width * config.TILE_LENGTH, w.height * config.TILE_LENGTH
    for p in w.rock_patches:
        assert p['x'] >= 0 and p['y'] >= 0
        assert p['x'] + p['size'] <= max_x
        assert p['y'] + p['size'] <= max_y


def test_ore_only_on_solid_rock_tiles():
    w = _small_world()
    for y in range(w.height):
        for x in range(w.width):
            if w.overlay_grid[y][x] is not None:
                assert w.map_grid[y][x] == 'stone', f'ore on non-rock tile {(x, y)}'


def test_diagonal_facing_does_not_flicker():
    # near-equal components (a diagonal, jittered by the net client's eased
    # 0.1px-rounded positions) must not flip the walk sprite every frame.
    pg.init()
    import movement
    p = Entity(load_prototype('player'), (0, 0))

    movement.update_player_animation(p, 3.0, 3.0)   # establish a facing on the diagonal
    start = p.anim.current_state
    assert start in ('walking_left', 'walking_right')

    seen = set()
    for dx, dy in [(3.0, 3.1), (3.1, 3.0), (2.9, 3.05), (3.05, 2.95)] * 6:
        movement.update_player_animation(p, dx, dy)
        seen.add(p.anim.current_state)
    assert seen == {start}, f'facing flickered across {seen}'


def test_facing_flips_when_the_other_axis_clearly_wins():
    pg.init()
    import movement
    p = Entity(load_prototype('player'), (0, 0))
    movement.update_player_animation(p, 3.0, 3.0)
    assert p.anim.current_state in ('walking_left', 'walking_right')
    movement.update_player_animation(p, 1.0, 3.0)   # vertical clearly dominates
    assert p.anim.current_state == 'walking_down'
