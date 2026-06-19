"""Gamified coding for ronin — XP, levels, streaks, and achievements earned for
*real* coding actions, on top of the arcade vibe.

Every meaningful thing you do (a test goes green, a bug dies, a commit lands, a
PR opens) is worth XP. XP climbs a ronin-flavoured ladder of titles, a daily
streak rewards showing up, and achievements unlock the first time you hit a
milestone. State lives at ``~/.ronin/gamify.json`` (honoring ``RONIN_HOME``, the
same override the run store, memory, and bushido use), so a brand-new ``ronin``
next week still knows how far you've climbed.

Design: the scoring/level/streak/achievement logic is a **pure, deterministic
core** — every helper takes the "today" date as a parameter rather than calling
``datetime.now`` itself, so it's trivially testable. A thin impure layer
(``record`` / ``render_profile``) is the only thing that touches disk or the
clock. Stdlib + rich only.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# palette (mirrors theme.py so every ronin surface looks the same)
# --------------------------------------------------------------------------- #
TEAL = "#2dd4bf"   # ronin brand / accent
GOOD = "#9ece6a"   # soft green — wins, filled XP
WARN = "#e0af68"   # soft amber — streak fire
SKY = "#7dd3fc"    # soft sky — badges / level
MUTE = "#6b7089"   # muted slate — secondary text

# --------------------------------------------------------------------------- #
# pure core: XP table
# --------------------------------------------------------------------------- #
# Points per real coding action. Tuned so the *hard, valuable* things (shipping a
# PR, killing a bug) dwarf the cheap ones (editing a file, playing an arcade
# round) — you level by doing real work, not by spamming.
XP_TABLE: dict[str, int] = {
    "pr_merged": 60,     # the whole point — work that shipped
    "pr_opened": 40,     # putting work up for review
    "bug_fixed": 25,     # a real defect, dead
    "refactor": 15,      # leaving it cleaner than you found it
    "test_passed": 10,   # a green check
    "test_written": 8,   # writing the safety net
    "commit": 5,         # a unit of progress saved
    "review_done": 5,    # reviewing someone else's work
    "file_edited": 3,    # touching code
    "game_played": 2,    # an arcade round — the vibe tax
}


def xp_for(event: str) -> int:
    """Points awarded for one occurrence of ``event``. Unknown event → 0. Pure."""
    return XP_TABLE.get(event, 0)


# --------------------------------------------------------------------------- #
# pure core: levels
# --------------------------------------------------------------------------- #
# Title for each level band, lowest first. Past the last named rank you stay a
# "Sword Saint" — the curve keeps numbering levels up forever, the title caps.
LEVEL_TITLES: tuple[str, ...] = (
    "Ronin Initiate",   # 1
    "Footsoldier",      # 2
    "Wandering Blade",  # 3
    "Duelist",          # 4
    "Swordsman",        # 5
    "Bushi",            # 6
    "Samurai",          # 7
    "Kensei",           # 8
    "Shogun",           # 9
    "Sword Saint",      # 10+
)


def level_for(total_xp: int) -> tuple[int, str]:
    """Map cumulative XP to a ``(level, title)`` pair. Pure.

    Curve: ``level = floor(sqrt(xp / 50)) + 1`` — a gentle quadratic, so each
    level costs a little more than the last (lvl 2 at 50 XP, lvl 3 at 200, lvl 4
    at 450, …). Negative/zero XP floors at level 1. The title is the band for the
    level, capped at the final rank.
    """
    xp = max(int(total_xp), 0)
    level = int(math.isqrt(xp // 50)) + 1
    title = LEVEL_TITLES[min(level, len(LEVEL_TITLES)) - 1]
    return level, title


def xp_for_level(level: int) -> int:
    """The minimum cumulative XP needed to *be* ``level`` — the inverse of the
    level curve. ``level<=1`` → 0. Pure; used to draw the progress-to-next bar."""
    if level <= 1:
        return 0
    return (level - 1) ** 2 * 50


def level_progress(total_xp: int) -> tuple[int, int, int, float]:
    """Progress through the current level. Pure.

    Returns ``(into_level, span_to_next, xp_to_next, fraction)`` where
    ``into_level`` is XP earned since this level began, ``span_to_next`` is the XP
    width of this level, ``xp_to_next`` is what's left to level up, and
    ``fraction`` is ``into_level / span_to_next`` clamped to ``[0, 1]``.
    """
    xp = max(int(total_xp), 0)
    level, _ = level_for(xp)
    floor_xp = xp_for_level(level)
    next_xp = xp_for_level(level + 1)
    span = max(next_xp - floor_xp, 1)
    into = xp - floor_xp
    to_next = max(next_xp - xp, 0)
    frac = min(max(into / span, 0.0), 1.0)
    return into, span, to_next, frac


# --------------------------------------------------------------------------- #
# pure core: streaks
# --------------------------------------------------------------------------- #
def update_streak(last_iso_date: str | None, today_iso: str) -> tuple[int, bool]:
    """Daily-streak transition. Pure — *you* supply "today", nothing is read from
    the clock, so it's deterministic.

    ``last_iso_date`` is the day the streak last advanced (``"YYYY-MM-DD"`` or
    ``None`` if you've never been active). ``today_iso`` is the current day.

    Returns ``(delta, continued)``:
      * **same day** as last       → ``(0, True)``  — already counted today.
      * **the very next day**      → ``(+1, True)`` — streak grows by one.
      * **a gap (or first ever)**  → reset; ``delta`` is ``1 - old_streak``
        so the caller can do ``streak += delta`` to land at exactly 1.
        ``continued`` is ``False`` (the run is broken / fresh).

    A malformed or future ``last`` date is treated as a fresh start.
    Note: ``delta`` is a transition to *add* to the stored streak, not the new
    streak itself — ``record`` clamps the resulting streak to ``>= 1``.
    """
    try:
        today = date.fromisoformat(today_iso)
    except (TypeError, ValueError):
        # No valid "today" → can't reason; treat as a single fresh day.
        return 1, False
    if not last_iso_date:
        return 1, False
    try:
        last = date.fromisoformat(last_iso_date)
    except (TypeError, ValueError):
        return 1, False
    gap = (today - last).days
    if gap == 0:
        return 0, True            # already active today, no change
    if gap == 1:
        return 1, True            # consecutive day, streak += 1
    # gap > 1 (missed a day) or gap < 0 (clock went backwards) → reset to 1.
    return 1, False


def apply_streak(prev_streak: int, last_iso_date: str | None, today_iso: str) -> tuple[int, bool, bool]:
    """Resolve a streak update against a known previous count. Pure helper that
    composes :func:`update_streak` for callers that hold the old streak.

    Returns ``(new_streak, continued, advanced)`` where ``advanced`` is True iff
    the streak counter actually moved today (so a same-day call is a no-op).
    """
    prev = max(int(prev_streak), 0)
    delta, continued = update_streak(last_iso_date, today_iso)
    if continued and delta == 0:
        return max(prev, 1), True, False     # same day — nothing changes
    if continued:
        return prev + delta, True, True       # consecutive — grow
    return 1, False, True                      # reset / fresh — land at 1


# --------------------------------------------------------------------------- #
# pure core: achievements
# --------------------------------------------------------------------------- #
# Each achievement is unlocked the first time its ``predicate(stats)`` is true,
# given a cumulative stats dict. ``stats`` keys mirror the per-event counters
# ``record`` maintains (``bug_fixed``, ``test_passed``, …) plus rollups:
#   ``xp`` (total), ``level``, ``streak`` (current), ``best_streak``,
#   ``events`` (total actions recorded).
ACHIEVEMENTS: list[dict[str, Any]] = [
    {
        "id": "first_blood",
        "name": "First Blood",
        "desc": "Fix your first bug.",
        "predicate": lambda s: s.get("bug_fixed", 0) >= 1,
    },
    {
        "id": "green_thumb",
        "name": "Green Thumb",
        "desc": "Pass your first test.",
        "predicate": lambda s: s.get("test_passed", 0) >= 1,
    },
    {
        "id": "first_steps",
        "name": "First Steps",
        "desc": "Record your very first action.",
        "predicate": lambda s: s.get("events", 0) >= 1,
    },
    {
        "id": "committed",
        "name": "Committed",
        "desc": "Land 10 commits.",
        "predicate": lambda s: s.get("commit", 0) >= 10,
    },
    {
        "id": "shipit",
        "name": "Ship It",
        "desc": "Open your first pull request.",
        "predicate": lambda s: s.get("pr_opened", 0) >= 1,
    },
    {
        "id": "bug_hunter",
        "name": "Bug Hunter",
        "desc": "Squash 10 bugs.",
        "predicate": lambda s: s.get("bug_fixed", 0) >= 10,
    },
    {
        "id": "centurion",
        "name": "Centurion",
        "desc": "Pass 100 tests.",
        "predicate": lambda s: s.get("test_passed", 0) >= 100,
    },
    {
        "id": "refactor_monk",
        "name": "Refactor Monk",
        "desc": "Perform 25 refactors.",
        "predicate": lambda s: s.get("refactor", 0) >= 25,
    },
    {
        "id": "week_warrior",
        "name": "Week Warrior",
        "desc": "Hold a 7-day coding streak.",
        "predicate": lambda s: max(s.get("streak", 0), s.get("best_streak", 0)) >= 7,
    },
    {
        "id": "unbroken",
        "name": "Unbroken",
        "desc": "Hold a 30-day coding streak.",
        "predicate": lambda s: max(s.get("streak", 0), s.get("best_streak", 0)) >= 30,
    },
    {
        "id": "samurai",
        "name": "Way of the Samurai",
        "desc": "Reach level 7 (Samurai).",
        "predicate": lambda s: s.get("level", 0) >= 7,
    },
    {
        "id": "sword_saint",
        "name": "Sword Saint",
        "desc": "Reach level 10 — the highest rank.",
        "predicate": lambda s: s.get("level", 0) >= 10,
    },
]


def check_achievements(stats: dict, already: set[str] | list[str] | None = None) -> list[str]:
    """Achievement ids newly satisfied by ``stats`` that aren't in ``already``.
    Pure — returns *new* unlocks in declaration order; pass the set you've already
    unlocked to avoid re-reporting them.
    """
    have = set(already or ())
    out: list[str] = []
    for ach in ACHIEVEMENTS:
        aid = ach["id"]
        if aid in have:
            continue
        try:
            if ach["predicate"](stats):
                out.append(aid)
        except Exception:  # noqa: BLE001 — a bad predicate must never break recording
            continue
    return out


def _achievement(aid: str) -> dict[str, Any] | None:
    return next((a for a in ACHIEVEMENTS if a["id"] == aid), None)


# --------------------------------------------------------------------------- #
# impure thin layer: persistence
# --------------------------------------------------------------------------- #
_MAX_RECENT = 30        # recent-events ring kept for the dashboard
_SCHEMA = 1


def _gamify_path() -> Path:
    """Location of the gamify save. Honors ``RONIN_HOME`` (same override memory,
    bushido, and the run store use), defaulting to ``~/.ronin/gamify.json``."""
    home = os.environ.get("RONIN_HOME")
    base = Path(home) if home else Path.home() / ".ronin"
    return base / "gamify.json"


def _blank_state() -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "xp": 0,
        "streak": 0,
        "best_streak": 0,
        "last_active": None,     # ISO date the streak last advanced
        "events": 0,             # total actions recorded
        "counts": {},            # per-event tallies (bug_fixed, test_passed, …)
        "unlocked": [],          # achievement ids, in unlock order
        "recent": [],            # ring of {event, n, xp, ts} for the dashboard
    }


def load_state() -> dict[str, Any]:
    """Read ``gamify.json``, healing missing keys. Never raises — a corrupt or
    absent file yields a blank profile."""
    p = _gamify_path()
    state = _blank_state()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                state.update({k: data[k] for k in state if k in data})
        except (OSError, ValueError):
            pass
    # normalize types defensively (hand-edited files happen)
    state["counts"] = dict(state.get("counts") or {})
    state["unlocked"] = list(state.get("unlocked") or [])
    state["recent"] = list(state.get("recent") or [])
    return state


def _save_state(state: dict[str, Any]) -> None:
    p = _gamify_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        state = dict(state)
        state["recent"] = state.get("recent", [])[-_MAX_RECENT:]
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _stats_view(state: dict[str, Any]) -> dict[str, Any]:
    """Flatten state into the dict shape achievement predicates expect."""
    level, _ = level_for(state.get("xp", 0))
    view = dict(state.get("counts") or {})
    view.update(
        xp=state.get("xp", 0),
        level=level,
        streak=state.get("streak", 0),
        best_streak=state.get("best_streak", 0),
        events=state.get("events", 0),
    )
    return view


def record(event: str, *, n: int = 1, today: str | None = None) -> dict[str, Any]:
    """Record ``n`` occurrences of ``event``: award XP, advance the daily streak,
    unlock any newly-earned achievements, persist, and report what changed.

    The only impure entry point for *earning* — it reads the clock (unless
    ``today`` is supplied, which the tests use for determinism) and writes
    ``gamify.json``. All the actual logic delegates to the pure helpers above.

    Returns a dict::

        {
          "event": str, "n": int,
          "xp_gained": int, "xp_total": int,
          "level": int, "title": str, "leveled_up": bool, "level_delta": int,
          "streak": int, "streak_continued": bool, "streak_advanced": bool,
          "unlocked": [ {id, name, desc}, … ],   # newly earned this call
        }
    """
    n = max(int(n), 0)
    today_iso = today or date.today().isoformat()
    state = load_state()

    before_level, _ = level_for(state["xp"])

    # 1) XP
    gained = xp_for(event) * n
    state["xp"] = int(state.get("xp", 0)) + gained

    # 2) counters
    state["events"] = int(state.get("events", 0)) + n
    if n:
        counts = state["counts"]
        counts[event] = int(counts.get(event, 0)) + n

    # 3) streak (pure transition against the stored last-active day)
    new_streak, continued, advanced = apply_streak(
        state.get("streak", 0), state.get("last_active"), today_iso
    )
    state["streak"] = new_streak
    state["best_streak"] = max(int(state.get("best_streak", 0)), new_streak)
    if advanced:
        state["last_active"] = today_iso

    # 4) level after the gain
    after_level, title = level_for(state["xp"])

    # 5) achievements (predicates see the post-update stats view)
    new_ids = check_achievements(_stats_view(state), state["unlocked"])
    if new_ids:
        state["unlocked"].extend(new_ids)

    # 6) recent-events ring for the dashboard
    if n:
        state["recent"].append(
            {"event": event, "n": n, "xp": gained, "ts": datetime.now().isoformat(timespec="seconds")}
        )

    _save_state(state)

    return {
        "event": event,
        "n": n,
        "xp_gained": gained,
        "xp_total": state["xp"],
        "level": after_level,
        "title": title,
        "leveled_up": after_level > before_level,
        "level_delta": after_level - before_level,
        "streak": new_streak,
        "streak_continued": continued,
        "streak_advanced": advanced,
        "unlocked": [
            {"id": a, "name": (_achievement(a) or {}).get("name", a),
             "desc": (_achievement(a) or {}).get("desc", "")}
            for a in new_ids
        ],
    }


# --------------------------------------------------------------------------- #
# impure thin layer: the dashboard
# --------------------------------------------------------------------------- #
def _xp_bar(frac: float, width: int = 28) -> str:
    """A teal/green XP bar as Rich markup. ``frac`` is clamped to ``[0, 1]``."""
    frac = min(max(frac, 0.0), 1.0)
    filled = int(round(frac * width))
    return f"[{GOOD}]{'█' * filled}[/{GOOD}][{MUTE}]{'░' * (width - filled)}[/{MUTE}]"


def _streak_flames(streak: int) -> str:
    """A little fire gauge that grows with the streak (capped so it never wraps)."""
    if streak <= 0:
        return f"[{MUTE}]no active streak[/{MUTE}]"
    flames = "🔥" * min(streak, 10)
    extra = f" [{MUTE}](+{streak - 10})[/{MUTE}]" if streak > 10 else ""
    return f"[{WARN}]{flames}[/{WARN}]{extra} [{WARN}]{streak}-day[/{WARN}]"


_EVENT_LABELS: dict[str, str] = {
    "pr_merged": "PR merged", "pr_opened": "PR opened", "bug_fixed": "bug fixed",
    "refactor": "refactor", "test_passed": "test passed", "test_written": "test written",
    "commit": "commit", "review_done": "review", "file_edited": "file edited",
    "game_played": "arcade round",
}


def render_profile(console, state: dict[str, Any] | None = None) -> None:
    """Draw the gamification dashboard — level + XP bar, current streak, unlocked
    badges, and recent events — in ronin's palette. Impure (loads state, prints).

    Pass ``state`` to render a known snapshot (the tests / ``record`` callers do
    this); otherwise it loads ``gamify.json``.
    """
    from rich.box import ROUNDED
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    if state is None:
        state = load_state()

    xp = int(state.get("xp", 0))
    level, title = level_for(xp)
    into, span, to_next, frac = level_progress(xp)
    streak = int(state.get("streak", 0))
    best = int(state.get("best_streak", 0))

    # --- header: rank + XP bar -------------------------------------------- #
    head = Table.grid(padding=(0, 1))
    head.add_column(justify="left", no_wrap=True)
    head.add_column(justify="left")
    head.add_row(
        Text(f"⚔ LV {level}", style=f"bold {TEAL}"),
        Text(title, style=f"bold {SKY}"),
    )
    at_cap = level >= len(LEVEL_TITLES)
    tail = (
        f"[{MUTE}]MAX — {xp:,} XP[/{MUTE}]"
        if at_cap
        else f"[{GOOD}]{into:,}[/{GOOD}][{MUTE}]/{span:,} XP  ·  {to_next:,} to LV {level + 1}[/{MUTE}]"
    )
    head.add_row(_xp_bar(frac), Text.from_markup(tail))
    head.add_row(
        Text.from_markup(f"[{MUTE}]total[/{MUTE}] [{GOOD}]{xp:,} XP[/{GOOD}]"),
        Text.from_markup(
            f"[{MUTE}]streak[/{MUTE}] {_streak_flames(streak)}"
            + (f"  [{MUTE}](best {best})[/{MUTE}]" if best > streak else "")
        ),
    )

    # --- badges ------------------------------------------------------------ #
    unlocked = list(state.get("unlocked") or [])
    have = set(unlocked)
    badge_tbl = Table.grid(padding=(0, 2))
    badge_tbl.add_column()
    badge_tbl.add_column()
    earned = [a for a in ACHIEVEMENTS if a["id"] in have]
    locked = [a for a in ACHIEVEMENTS if a["id"] not in have]
    badge_tbl.add_row(
        Text.from_markup(f"[bold {SKY}]🏅 Badges[/bold {SKY}]  "
                         f"[{MUTE}]{len(earned)}/{len(ACHIEVEMENTS)}[/{MUTE}]"),
        Text(""),
    )
    if earned:
        for a in earned:
            badge_tbl.add_row(
                Text.from_markup(f"[{GOOD}]◆ {a['name']}[/{GOOD}]"),
                Text.from_markup(f"[{MUTE}]{a['desc']}[/{MUTE}]"),
            )
    # show the next couple of locked goals as carrots
    for a in locked[:3]:
        badge_tbl.add_row(
            Text.from_markup(f"[{MUTE}]◇ {a['name']}[/{MUTE}]"),
            Text.from_markup(f"[{MUTE}]{a['desc']}[/{MUTE}]"),
        )

    # --- recent events ----------------------------------------------------- #
    recent = list(state.get("recent") or [])[-8:][::-1]
    recent_tbl = Table.grid(padding=(0, 2))
    recent_tbl.add_column()
    recent_tbl.add_column(justify="right")
    recent_tbl.add_row(Text.from_markup(f"[bold {SKY}]⌁ Recent[/bold {SKY}]"), Text(""))
    if recent:
        for ev in recent:
            label = _EVENT_LABELS.get(ev.get("event", ""), ev.get("event", "?"))
            mult = f" ×{ev['n']}" if ev.get("n", 1) > 1 else ""
            recent_tbl.add_row(
                Text.from_markup(f"[{TEAL}]⏺[/{TEAL}] [{MUTE}]{label}{mult}[/{MUTE}]"),
                Text.from_markup(f"[{GOOD}]+{ev.get('xp', 0)}[/{GOOD}]"),
            )
    else:
        recent_tbl.add_row(
            Text.from_markup(f"[{MUTE}]nothing yet — go fix a bug ⚔[/{MUTE}]"), Text("")
        )

    body = Table.grid(padding=(1, 0))
    body.add_column()
    body.add_row(head)
    body.add_row(badge_tbl)
    body.add_row(recent_tbl)

    console.print(
        Panel(
            body,
            title=f"[bold {TEAL}]ronin · the way[/bold {TEAL}]",
            subtitle=f"[{MUTE}]gamified coding[/{MUTE}]",
            border_style=TEAL,
            box=ROUNDED,
            padding=(1, 2),
        )
    )


# --------------------------------------------------------------------------- #
# convenience: a one-line status (e.g. for a status bar or `record` echo)
# --------------------------------------------------------------------------- #
def status_line(state: dict[str, Any] | None = None) -> str:
    """A compact one-line summary as Rich markup: ``LV n Title · X XP · 🔥 d-day``.
    Loads state if not given. Handy for a status bar or a post-action echo."""
    if state is None:
        state = load_state()
    xp = int(state.get("xp", 0))
    level, title = level_for(xp)
    streak = int(state.get("streak", 0))
    fire = f" · [{WARN}]🔥{streak}d[/{WARN}]" if streak > 0 else ""
    return (f"[bold {TEAL}]LV {level}[/bold {TEAL}] [{SKY}]{title}[/{SKY}] "
            f"[{MUTE}]·[/{MUTE}] [{GOOD}]{xp:,} XP[/{GOOD}]{fire}")
