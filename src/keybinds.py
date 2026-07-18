
# rebindable controls: resolve the action->key-name map in settings.json into
# the integer pygame key codes the input code compares against. ONE shared
# resolver used by the Client (both single-player and multiplayer) and the
# authoritative host, so the control mapping can't drift.
#
# settings.json stores key NAMES ('w', 'tab', 'escape', '`') because they're
# human-editable; load_keybinds() turns them into codes and back-fills any
# action the user's file left out or mis-typed. call it after pygame is
# initialized — pg.key.key_code needs the video system up.

import pygame as pg

from settings import load_settings, DEFAULT_KEYBINDS


def _resolve(name):
    try:
        return pg.key.key_code(str(name))
    except (ValueError, TypeError):
        return None


def load_keybinds() -> dict:
    # action -> key code. per-action override from settings; default on miss.
    names = {**DEFAULT_KEYBINDS, **(load_settings().get('keybinds') or {})}
    out = {}
    for action, default_name in DEFAULT_KEYBINDS.items():
        code = _resolve(names.get(action, default_name))
        out[action] = code if code is not None else _resolve(default_name)
    return out


def pressed(keys, code) -> bool:
    # safe held-key test against pg.key.get_pressed(). letter codes are small
    # and in range, but a user could rebind movement to a high-code key (F-keys,
    # arrows) that's past the array end — guard the index so it can't crash.
    return 0 <= code < len(keys) and bool(keys[code])
