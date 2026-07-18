
# gameplay balance tunables, sourced from data/balance.json so numbers can be
# retuned without touching code (the rock/world-gen knobs especially get
# iterated on a lot). the file is optional: any missing or malformed key falls
# back to the default below, and unknown keys are ignored — so a stale or
# hand-broken balance.json can never crash boot or silently mis-set a value.
#
# each module still imports the UPPERCASE constant it already used; only the
# *definition* moved here, so call sites are unchanged.

import json

from config import DATA_DIR

_BALANCE_FILE = f'{DATA_DIR}/balance.json'

_defaults = {
    # combat / xp
    'damage_min': 2,
    'damage_max': 3,
    'combat_xp_per_damage': 4.0,
    'health_xp_fraction': 0.34,
    'kill_xp_per_level': 10.0,
    'health_bar_visible_ms': 6000,
    'float_lifetime_ms': 850,
    'float_rise_px': 30,
    # mobs
    'chase_repath_sec': 0.4,
    'wander_tile_radius': 6,
    'wander_pause_range': [1.5, 4.0],
    # contracts / economy
    'board_size': 6,
    'qty_range': [5, 30],
    'margin_range': [1.0, 1.3],
    'collateral_ratio': 1.5,
    'resource_out_prob': 0.7,
    # knockback
    'knockback_tiles': 2.5,
    'knockback_decay': 14.0,
    # netcode smoothing (client-side feel)
    'predict_correct': 10.0,
    'interp_rate': 12.0,
    'snap_dist': 96.0,
    # rock / world generation
    'rock_stone_cov': 0.5,
    'rock_ore_cov': 0.85,
    'rock_ore_rate': 0.35,
    'rock_cov_res': 3,
    'rock_octaves': 5,
    'rock_warp': 0.32,
    'rock_base_level': 0.34,
    'rock_edge_bias': 0.62,
}


def _load() -> dict:
    data = dict(_defaults)
    try:
        with open(_BALANCE_FILE) as f:
            loaded = json.load(f)
        # only accept known keys, so a typo in the file is ignored (default wins)
        data.update({k: v for k, v in loaded.items() if k in _defaults})
    except (OSError, json.JSONDecodeError):
        pass
    return data


_b = _load()

# --- combat / xp ---
DAMAGE_MIN = _b['damage_min']
DAMAGE_MAX = _b['damage_max']
COMBAT_XP_PER_DAMAGE = _b['combat_xp_per_damage']
HEALTH_XP_FRACTION = _b['health_xp_fraction']
KILL_XP_PER_LEVEL = _b['kill_xp_per_level']
HEALTH_BAR_VISIBLE_MS = _b['health_bar_visible_ms']
FLOAT_LIFETIME_MS = _b['float_lifetime_ms']
FLOAT_RISE_PX = _b['float_rise_px']

# --- mobs ---
CHASE_REPATH_SEC = _b['chase_repath_sec']
WANDER_TILE_RADIUS = _b['wander_tile_radius']
WANDER_PAUSE_RANGE = tuple(_b['wander_pause_range'])

# --- contracts / economy ---
BOARD_SIZE = _b['board_size']
QTY_RANGE = tuple(_b['qty_range'])
MARGIN_RANGE = tuple(_b['margin_range'])
COLLATERAL_RATIO = _b['collateral_ratio']
RESOURCE_OUT_PROB = _b['resource_out_prob']

# --- knockback ---
KNOCKBACK_TILES = _b['knockback_tiles']
KNOCKBACK_DECAY = _b['knockback_decay']

# --- netcode smoothing ---
PREDICT_CORRECT = _b['predict_correct']
INTERP_RATE = _b['interp_rate']
SNAP_DIST = _b['snap_dist']

# --- rock / world generation ---
ROCK_STONE_COV = _b['rock_stone_cov']
ROCK_ORE_COV = _b['rock_ore_cov']
ROCK_ORE_RATE = _b['rock_ore_rate']
ROCK_COV_RES = _b['rock_cov_res']
ROCK_OCTAVES = _b['rock_octaves']
ROCK_WARP = _b['rock_warp']
ROCK_BASE_LEVEL = _b['rock_base_level']
ROCK_EDGE_BIAS = _b['rock_edge_bias']
