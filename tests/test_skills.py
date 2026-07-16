
# skills.py — pure progression module. no world/pygame needed.

import skills


def test_fresh_skills_has_every_track_at_zero():
    s = skills.fresh_skills()
    assert set(s) == set(skills.SKILLS)
    assert all(v == 0 for v in s.values())
    assert all(skills.level_of(s, name) == 1 for name in skills.SKILLS)


def test_xp_curve_is_monotonic_and_clamped():
    xps = [skills.xp_for_level(lvl) for lvl in range(1, skills.LEVEL_CAP + 1)]
    assert xps[0] == 0                       # level 1 costs nothing
    assert all(b > a for a, b in zip(xps, xps[1:]))   # strictly increasing
    # out-of-range levels clamp rather than explode
    assert skills.xp_for_level(0) == 0
    assert skills.xp_for_level(-5) == 0
    assert skills.xp_for_level(999) == skills.xp_for_level(skills.LEVEL_CAP)


def test_level_for_xp_round_trips_every_level():
    for lvl in range(1, skills.LEVEL_CAP + 1):
        assert skills.level_for_xp(skills.xp_for_level(lvl)) == lvl


def test_level_for_xp_between_thresholds_and_beyond_cap():
    # one xp short of a level stays at the lower level; one past reaches it
    l5 = skills.xp_for_level(5)
    assert skills.level_for_xp(l5 - 1) == 4
    assert skills.level_for_xp(l5) == 5
    # arbitrarily large xp never exceeds the cap
    assert skills.level_for_xp(10 ** 12) == skills.LEVEL_CAP


def test_grant_mutates_and_reports_level_ups():
    s = skills.fresh_skills()
    # enough to cross from level 1 into level 3
    got = skills.grant(s, 'mining', skills.xp_for_level(3))
    assert s['mining'] == skills.xp_for_level(3)
    assert got == [('mining', 2), ('mining', 3)]


def test_grant_without_level_up_returns_empty():
    s = skills.fresh_skills()
    assert skills.grant(s, 'combat', 1) == []          # not enough to hit lvl 2
    assert skills.level_of(s, 'combat') == 1


def test_grant_is_noop_for_unknown_skill_or_nonpositive():
    s = skills.fresh_skills()
    assert skills.grant(s, 'fishing', 500) == []        # not a real track
    assert 'fishing' not in s
    assert skills.grant(s, 'combat', 0) == []
    assert skills.grant(s, 'combat', -10) == []
    assert s['combat'] == 0


def test_effects_match_level_one_baseline():
    assert skills.max_hp_for(1) == 30                    # player.json max_health
    assert skills.damage_range_for(1) == (2, 3)         # combat DAMAGE_MIN/MAX
    assert skills.break_time_scale(1) == 1.0
    assert skills.yield_bonus(1) == 0


def test_effects_scale_up_and_stay_bounded():
    assert skills.max_hp_for(50) > skills.max_hp_for(1)
    lo, hi = skills.damage_range_for(50)
    assert lo > 2 and hi > 3
    # break time only ever gets faster, and never below the 0.4 floor
    assert skills.break_time_scale(50) < 1.0
    assert skills.break_time_scale(999) >= 0.4
    assert skills.yield_bonus(50) >= skills.yield_bonus(1)
