
# skills: XP curve + level effects for the player's four progression tracks.
#
# pure module — no pygame, no world, no project imports — so it unit-tests in
# isolation and the eventual server sim can call the same grant() with no
# rework (XP is SP-local for now; server-authority is a later bolt-on).
#
# a player carries a 'skills' component: a flat {skill_name: total_xp} dict
# (see Entity + fresh_skills). everything derived — level, max hp, damage,
# mine speed, yield — comes from the raw xp through the helpers here, so this
# file is the single knob for progression balance.

import bisect


SKILLS = ('combat', 'health', 'mining', 'farming')

# player-facing labels. internal keys stay stable (saves, prototype fields), so
# renaming a skill for the ui is a one-line change here.
DISPLAY_NAMES = {
    'combat': 'Combat',
    'health': 'Health',
    'mining': 'Mining',
    'farming': 'Agriculture',
}


def display_name(skill: str) -> str:
    return DISPLAY_NAMES.get(skill, skill.capitalize())

# levels run 1..LEVEL_CAP. shorter and punchier than RuneScape's 99 so a level
# means more; the curve below is our own, not RS's table.
LEVEL_CAP = 50

# xp(level) = _XP_K * (level - 1) ** _XP_EXP, so level 1 costs 0 and each level
# costs progressively more. tuned so ~level 10 is an afternoon and ~level 50 is
# a long-haul grind. all three constants are free to retune.
_XP_K = 40.0
_XP_EXP = 2.5


def xp_for_level(level: int) -> int:
    # total accumulated xp required to *be* `level`. clamped to [1, LEVEL_CAP].
    level = max(1, min(int(level), LEVEL_CAP))
    if level <= 1:
        return 0
    return int(_XP_K * (level - 1) ** _XP_EXP)


# cumulative thresholds, index i => xp needed for level (i + 1). monotonic, so
# level_for_xp can binary-search it.
_XP_TABLE = [xp_for_level(lvl) for lvl in range(1, LEVEL_CAP + 1)]


def level_for_xp(xp: float) -> int:
    # highest level whose xp threshold is <= xp. clamped to [1, LEVEL_CAP].
    i = bisect.bisect_right(_XP_TABLE, xp) - 1
    return max(1, min(i + 1, LEVEL_CAP))


def fresh_skills() -> dict:
    # a brand-new player's skills component: every track at 0 xp (level 1).
    return {s: 0 for s in SKILLS}


def level_of(skills: dict, skill: str) -> int:
    return level_for_xp(skills.get(skill, 0))


def grant(skills: dict, skill: str, amount: float) -> list:
    # add `amount` xp to one track (in place) and return the list of levels
    # newly crossed as (skill, level) pairs — feeds the level-up toast. no-op
    # (returns []) for an unknown skill or non-positive amount, so shared combat
    # code can call it blindly on non-player / accountless-server attackers.
    if skill not in skills or amount <= 0:
        return []
    before = level_for_xp(skills[skill])
    skills[skill] += int(amount)
    after = level_for_xp(skills[skill])
    return [(skill, lvl) for lvl in range(before + 1, after + 1)]


# --- level effects (the "why leveling matters") ----------------------------
# level-1 values below intentionally match current balance (player max_health
# 30, combat damage 2-3, break time unscaled, no yield bonus), so introducing
# skills changes nothing at level 1 and only ever buffs from there.

BASE_HP = 30
HP_PER_LEVEL = 2


def max_hp_for(level: int) -> int:
    # Health track -> the player's max hp. level 1 == the player prototype's 30.
    return BASE_HP + (max(1, level) - 1) * HP_PER_LEVEL


BASE_DAMAGE = (2, 3)


def damage_range_for(level: int) -> tuple:
    # Combat track -> the (min, max) melee damage roll. +1 to both ends every
    # 3 levels. level 1 == combat.DAMAGE_MIN/MAX (2, 3).
    bonus = (max(1, level) - 1) // 3
    return (BASE_DAMAGE[0] + bonus, BASE_DAMAGE[1] + bonus)


def break_time_scale(level: int) -> float:
    # Mining track -> multiplier on an ore's break_time (lower = faster). level
    # 1 == 1.0 (unchanged); floored so high levels never trivialize a break.
    return max(0.4, 1.0 - (max(1, level) - 1) * 0.012)


def yield_bonus(level: int) -> int:
    # Farming track -> extra guaranteed units added to a mature harvest. level
    # 1 == 0 (unchanged); +1 every 10 levels.
    return (max(1, level) - 1) // 10
