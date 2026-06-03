
# load and persist runtime settings (window size, fullscreen, hud toggle, fps cap)

import json
import os
from config import (
    SETTINGS_FILE, SCREEN_WIDTH, SCREEN_HEIGHT, FPS,
)


# display_mode controls how the surface is opened:
#   'windowed'   -> standard window at (screen_width, screen_height)
#   'fullscreen' -> exclusive fullscreen at (screen_width, screen_height)
#   'borderless' -> NOFRAME at the desktop's native resolution
DISPLAY_MODES = ('windowed', 'fullscreen', 'borderless')


_defaults = {
    'screen_width': SCREEN_WIDTH,
    'screen_height': SCREEN_HEIGHT,
    'display_mode': 'windowed',
    'show_hud': True,
    'fps_cap': FPS,
}


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return dict(_defaults)
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(_defaults)
    # migrate legacy `fullscreen: bool` to the new `display_mode` field.
    # we keep the field around in the in-memory dict so save_settings
    # writes the canonical shape on the next persist.
    if 'display_mode' not in data:
        if data.pop('fullscreen', False):
            data['display_mode'] = 'fullscreen'
        else:
            data['display_mode'] = 'windowed'
    elif data['display_mode'] not in DISPLAY_MODES:
        data['display_mode'] = 'windowed'
    # merge so a partial file doesn't drop defaults
    merged = dict(_defaults)
    merged.update(data)
    return merged


def save_settings(settings: dict):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
    except OSError:
        # non-fatal: settings just won't persist this session
        pass
