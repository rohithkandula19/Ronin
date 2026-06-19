"""Tests for gamified coding — XP table, level curve, daily streaks, and
achievements. All the pure helpers are deterministic (dates are passed in), so
no clock mocking is needed; the impure ``record`` is exercised against a
``RONIN_HOME`` temp dir.
"""
from __future__ import annotations

import json
from pathlib import Path

from ronin_cli import gamify
from ronin_cli.gamify import (
    ACHIEVEMENTS,
    LEVEL_TITLES,
    check_achievements,
    level_for,
    level_progress,
    update_streak,
    xp_for,
    xp_for_level,
)


# --------------------------------------------------------------------------- #
# xp_for
# --------------------------------------------------------------------------- #
def test_xp_for_known_events() -> None:
    assert xp_for("test_passed") == 10
    assert xp_for("bug_fixed") == 25
    assert xp_for("commit") == 5
    assert xp_for("pr_opened") == 40
    assert xp_for("refactor") == 15
    assert xp_for("file_edited") == 3
    assert xp_for("game_played") == 2


def test_xp_for_unknown_event_is_zero() -> None:
    assert xp_for("nonsense") == 0
    assert xp_for("") == 0


def test_xp_table_ordering_hard_beats_cheap() -> None:
    # the valuable actions must be worth more than the cheap ones
    assert xp_for("pr_merged") > xp_for("pr_opened") > xp_for("bug_fixed")
    assert xp_for("bug_fixed") > xp_for("test_passed") > xp_for("commit")
    assert xp_for("commit") > xp_for("file_edited") > xp_for("game_played")


# --------------------------------------------------------------------------- #
# level_for / curve
# --------------------------------------------------------------------------- #
def test_level_for_floor_and_low_band() -> None:
    assert level_for(0) == (1, "Ronin Initiate")
    assert level_for(-100) == (1, "Ronin Initiate")   # negative floors at 1
    assert level_for(49)[0] == 1                       # just under level 2
    assert level_for(50)[0] == 2                       # exactly level 2
    assert level_for(199)[0] == 2
    assert level_for(200)[0] == 3                       # 2^2 * 50


def test_level_for_curve_matches_formula() -> None:
    import math
    for xp in (0, 1, 49, 50, 200, 450, 800, 1250, 5000, 12_345):
        expected = int(math.isqrt(xp // 50)) + 1
        assert level_for(xp)[0] == expected


def test_level_for_500_is_level_4() -> None:
    # sqrt(500/50) = sqrt(10) ≈ 3.16 → floor 3 → level 4
    level, title = level_for(500)
    assert level == 4
    assert title == "Duelist"


def test_level_titles_rise_to_sword_saint() -> None:
    assert level_for(0)[1] == "Ronin Initiate"
    # the cap rank: level 10 needs (10-1)^2 * 50 = 4050 XP
    assert level_for(4050) == (10, "Sword Saint")
    # past the named ranks you stay Sword Saint, level keeps climbing
    big_level, big_title = level_for(1_000_000)
    assert big_title == "Sword Saint"
    assert big_level > len(LEVEL_TITLES)


def test_xp_for_level_inverts_curve() -> None:
    # the floor XP of a level must land you exactly on that level
    for lvl in range(1, 12):
        floor_xp = xp_for_level(lvl)
        assert level_for(floor_xp)[0] == lvl
        if floor_xp > 0:
            assert level_for(floor_xp - 1)[0] == lvl - 1   # one less → previous level


def test_level_progress_fraction() -> None:
    into, span, to_next, frac = level_progress(50)   # exactly at level 2 start
    assert into == 0
    assert to_next == span                            # nothing earned into the level yet
    assert frac == 0.0
    # halfway-ish through level 2 (50..200 spans 150)
    into, span, to_next, frac = level_progress(125)
    assert span == 150
    assert into == 75
    assert to_next == 75
    assert abs(frac - 0.5) < 1e-9


# --------------------------------------------------------------------------- #
# update_streak
# --------------------------------------------------------------------------- #
def test_update_streak_same_day_no_change() -> None:
    assert update_streak("2026-06-18", "2026-06-18") == (0, True)


def test_update_streak_consecutive_day_increments() -> None:
    assert update_streak("2026-06-18", "2026-06-19") == (1, True)


def test_update_streak_gap_resets() -> None:
    delta, continued = update_streak("2026-06-15", "2026-06-19")   # 4-day gap
    assert continued is False
    assert delta == 1


def test_update_streak_first_ever_is_fresh() -> None:
    assert update_streak(None, "2026-06-19") == (1, False)


def test_update_streak_month_boundary_is_consecutive() -> None:
    assert update_streak("2026-05-31", "2026-06-01") == (1, True)


def test_update_streak_clock_backwards_resets() -> None:
    delta, continued = update_streak("2026-06-19", "2026-06-18")   # yesterday < last
    assert continued is False
    assert delta == 1


def test_update_streak_bad_dates_fresh() -> None:
    assert update_streak("not-a-date", "2026-06-19") == (1, False)
    assert update_streak("2026-06-18", "garbage") == (1, False)


def test_apply_streak_composition() -> None:
    # consecutive day off a 4-streak → 5, advanced
    assert gamify.apply_streak(4, "2026-06-18", "2026-06-19") == (5, True, True)
    # same day → unchanged, not advanced
    assert gamify.apply_streak(4, "2026-06-19", "2026-06-19") == (4, True, False)
    # gap → reset to 1, advanced (a new day began)
    assert gamify.apply_streak(9, "2026-06-10", "2026-06-19") == (1, False, True)


# --------------------------------------------------------------------------- #
# check_achievements
# --------------------------------------------------------------------------- #
def test_check_achievements_first_blood_and_green_thumb() -> None:
    got = check_achievements({"bug_fixed": 1, "test_passed": 1, "events": 2})
    assert "first_blood" in got
    assert "green_thumb" in got


def test_check_achievements_centurion_needs_100() -> None:
    assert "centurion" not in check_achievements({"test_passed": 99})
    assert "centurion" in check_achievements({"test_passed": 100})


def test_check_achievements_week_warrior_on_streak() -> None:
    assert "week_warrior" not in check_achievements({"streak": 6})
    assert "week_warrior" in check_achievements({"streak": 7})
    # also satisfied by a historical best even if the current streak dropped
    assert "week_warrior" in check_achievements({"streak": 1, "best_streak": 7})


def test_check_achievements_excludes_already_unlocked() -> None:
    stats = {"bug_fixed": 5, "events": 5}
    first = check_achievements(stats)
    assert "first_blood" in first
    again = check_achievements(stats, already=first)
    assert "first_blood" not in again


def test_check_achievements_returns_declaration_order() -> None:
    # everything unlocked at once → order must follow ACHIEVEMENTS, no dupes
    big = {a["id"] for a in ACHIEVEMENTS}
    stats = {
        "bug_fixed": 100, "test_passed": 100, "commit": 100, "pr_opened": 100,
        "refactor": 100, "events": 500, "streak": 30, "best_streak": 30, "level": 10,
    }
    got = check_achievements(stats)
    assert set(got) == big
    order = [a["id"] for a in ACHIEVEMENTS]
    assert got == [aid for aid in order if aid in set(got)]


def test_achievement_records_are_well_formed() -> None:
    seen_ids: set[str] = set()
    for a in ACHIEVEMENTS:
        assert {"id", "name", "desc", "predicate"} <= set(a)
        assert a["id"] not in seen_ids, "duplicate achievement id"
        seen_ids.add(a["id"])
        assert callable(a["predicate"])


# --------------------------------------------------------------------------- #
# impure layer: record + load (RONIN_HOME temp dir, deterministic via today=)
# --------------------------------------------------------------------------- #
def test_record_round_trip_and_unlocks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))

    r1 = gamify.record("bug_fixed", today="2026-06-18")
    assert r1["xp_gained"] == 25
    assert r1["xp_total"] == 25
    assert r1["streak"] == 1
    assert any(u["id"] == "first_blood" for u in r1["unlocked"])

    # file actually written under RONIN_HOME
    saved = tmp_path / "gamify.json"
    assert saved.is_file()
    data = json.loads(saved.read_text())
    assert data["xp"] == 25
    assert data["counts"]["bug_fixed"] == 1
    assert "first_blood" in data["unlocked"]

    # next day, same event → streak advances, no duplicate achievement
    r2 = gamify.record("bug_fixed", today="2026-06-19")
    assert r2["xp_total"] == 50
    assert r2["streak"] == 2
    assert r2["unlocked"] == []     # first_blood already earned


def test_record_same_day_does_not_grow_streak(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    gamify.record("commit", today="2026-06-18")
    r = gamify.record("commit", today="2026-06-18")
    assert r["streak"] == 1
    assert r["streak_advanced"] is False
    assert r["xp_total"] == 10       # two commits, 5 each


def test_record_n_multiplies_xp_and_counts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    r = gamify.record("test_passed", n=100, today="2026-06-18")
    assert r["xp_gained"] == 1000
    assert any(u["id"] == "centurion" for u in r["unlocked"])
    assert any(u["id"] == "green_thumb" for u in r["unlocked"])


def test_record_level_up_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    # 50 XP crosses from level 1 → 2
    r = gamify.record("test_passed", n=5, today="2026-06-18")   # 50 XP
    assert r["xp_total"] == 50
    assert r["level"] == 2
    assert r["leveled_up"] is True
    assert r["level_delta"] == 1


def test_load_state_blank_when_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    st = gamify.load_state()
    assert st["xp"] == 0
    assert st["unlocked"] == []
    assert st["streak"] == 0


def test_render_profile_smoke(tmp_path: Path, monkeypatch) -> None:
    # render must not raise on a populated profile (no-output console)
    from rich.console import Console

    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    gamify.record("bug_fixed", today="2026-06-18")
    gamify.record("commit", n=3, today="2026-06-19")
    console = Console(file=open(tmp_path / "out.txt", "w", encoding="utf-8"), width=80)
    gamify.render_profile(console)
    console.file.close()
    out = (tmp_path / "out.txt").read_text()
    assert "LV" in out and "Badges" in out
