
# centralized image cache. callers ask for a path; repeated requests
# return the same cached Surface. missing files render as a bright magenta
# square so the issue is loud and visible in-game.
#
# this module assumes pg.display.set_mode() has already been called by the
# time load_image fires, since convert_alpha() requires a video surface.
# (Screen() in render.py handles that during game startup.)

import os
import pygame as pg


_cache: dict[str, pg.Surface] = {}


def load_image(path: str) -> pg.Surface:
    cached = _cache.get(path)
    if cached is not None:
        return cached
    if not os.path.exists(path):
        surf = pg.Surface((32, 32))
        surf.fill((255, 0, 255))
        print(f'warning: missing image {path}')
    else:
        surf = pg.image.load(path).convert_alpha()
    _cache[path] = surf
    return surf
