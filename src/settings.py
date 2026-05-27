
# load and persist runtime settings (window size, fullscreen, hud toggle, fps cap)

import json
import os
from config import (
    SETTINGS_FILE, SCREEN_WIDTH, SCREEN_HEIGHT, FPS,
)


_defaults = {
    'screen_width': SCREEN_WIDTH,
    'screen_height': SCREEN_HEIGHT,
    'fullscreen': False,
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
