"""ronin stats — a beautiful terminal usage dashboard, the way Claude's app home
screen feels: a row of headline stat cards (sessions, messages, total tokens,
active days, streaks, peak hour, favourite model), a GitHub-style contribution
heatmap of your activity, a playful one-liner ("you've used ~Nx more tokens than
*Animal Farm*"), and the ronin red-panda wordmark for a little delight.

Design — mirrors ``gamify.py``: the aggregation/streak/heatmap maths is a **pure,
deterministic core** (every function takes its dates/timestamps as parameters and
never reads the clock), so it's trivially testable. A thin impure layer
(``load_records`` / ``render``) is the only thing that touches disk. Stdlib +
rich only.

Privacy by construction: we aggregate **counts and token totals only** — session
transcripts are read solely to *count* turns/messages; their content is never
read, stored, or printed.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# palette (mirrors theme.py / gamify.py so every ronin surface matches)
# --------------------------------------------------------------------------- #
TEAL = "#2dd4bf"   # ronin brand / accent
CYAN = "#22d3ee"
GOOD = "#9ece6a"   # soft green
WARN = "#e0af68"   # soft amber — streak fire
SKY = "#7dd3fc"    # soft sky — labels / model
MUTE = "#6b7089"   # muted slate — secondary text
SOFT = "#9aa0b8"   # soft foreground

# Five teal shades for the heatmap, level 0 (empty) → level 4 (hottest). The
# empty cell is a muted slate so the grid reads even with no activity.
HEAT_COLORS: tuple[str, ...] = ("#23304a", "#0f4a44", "#147d72", "#1fb5a3", "#2dd4bf")
# Shaded block glyphs paired with the colours, so the grid still reads on a
# monochrome / low-colour terminal (intensity legible by glyph alone).
HEAT_GLYPHS: tuple[str, ...] = ("·", "░", "▒", "▓", "█")


# --------------------------------------------------------------------------- #
# pure core: a tiny date helper
# --------------------------------------------------------------------------- #
def _as_date(s: str) -> date | None:
    """Parse a ``YYYY-MM-DD`` (or longer ISO ``...THH:MM...``) string to a date.
    Returns ``None`` on anything malformed — callers skip bad rows, never crash."""
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def _day_of(rec: dict) -> str | None:
    """The ``YYYY-MM-DD`` day a usage record belongs to. Accepts either a ``date``
    field or an ISO ``timestamp`` (what ``usage.record_usage`` writes). ``None``
    if neither is present/parseable."""
    raw = rec.get("date") or rec.get("timestamp") or rec.get("ts") or ""
    d = _as_date(str(raw))
    return d.isoformat() if d else None


def _hour_of(rec: dict) -> int | None:
    """Hour-of-day (0-23, local to the stored timestamp) for a record, or ``None``.
    Reads ``hour`` if given, else the ``HH`` of an ISO timestamp."""
    if isinstance(rec.get("hour"), int) and 0 <= rec["hour"] <= 23:
        return rec["hour"]
    raw = str(rec.get("timestamp") or rec.get("ts") or "")
    # ISO is "YYYY-MM-DDTHH:MM:SS..."; the hour is chars 11-13 after a 'T'/space.
    if len(raw) >= 13 and raw[10] in "T ":
        try:
            h = int(raw[11:13])
        except ValueError:
            return None
        if 0 <= h <= 23:
            return h
    return None


# --------------------------------------------------------------------------- #
# pure core: aggregate
# --------------------------------------------------------------------------- #
def aggregate(records: list[dict]) -> dict:
    """Roll usage records up into the headline figures the dashboard shows. Pure.

    Each record is a loose dict ``{timestamp|date, provider, model,
    input_tokens, output_tokens, command, ...}`` (the shape
    ``usage.record_usage`` writes). Missing / malformed fields are tolerated —
    a bad row contributes nothing rather than raising.

    Returns a dict::

        {
          "records": int,                 # usable usage rows seen
          "input_tokens": int, "output_tokens": int, "total_tokens": int,
          "by_model": {model: count}, "favorite_model": str|None,
          "by_provider": {provider: count}, "favorite_provider": str|None,
          "by_command": {command: count}, "favorite_command": str|None,
          "active_dates": [YYYY-MM-DD, …]  # sorted unique active days
          "date_counts": {YYYY-MM-DD: int},# rows per day (for the heatmap)
          "hours": [int, …],               # hour-of-day per row (for peak_hour)
          "first_day": str|None, "last_day": str|None,
        }
    """
    in_tok = out_tok = used = 0
    by_model: Counter[str] = Counter()
    by_provider: Counter[str] = Counter()
    by_command: Counter[str] = Counter()
    date_counts: Counter[str] = Counter()
    hours: list[int] = []

    for rec in records:
        if not isinstance(rec, dict):
            continue
        used += 1
        # tokens (coerce defensively; a string "120" or a None must not crash)
        in_tok += _safe_int(rec.get("input_tokens"))
        out_tok += _safe_int(rec.get("output_tokens"))
        model = (rec.get("model") or "").strip()
        if model:
            by_model[model] += 1
        provider = (rec.get("provider") or "").strip()
        if provider:
            by_provider[provider] += 1
        command = (rec.get("command") or "").strip()
        if command:
            by_command[command] += 1
        day = _day_of(rec)
        if day:
            date_counts[day] += 1
        hr = _hour_of(rec)
        if hr is not None:
            hours.append(hr)

    return {
        "records": used,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "by_model": dict(by_model),
        "favorite_model": _top(by_model),
        "by_provider": dict(by_provider),
        "favorite_provider": _top(by_provider),
        "by_command": dict(by_command),
        "favorite_command": _top(by_command),
        "active_dates": sorted(date_counts),
        "date_counts": dict(date_counts),
        "hours": hours,
        "first_day": min(date_counts) if date_counts else None,
        "last_day": max(date_counts) if date_counts else None,
    }


def _safe_int(value: Any) -> int:
    """Coerce ``value`` to a non-negative int; anything unparseable → 0."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _top(counter: Counter[str]) -> str | None:
    """The most common key in a Counter, ties broken alphabetically for
    determinism (``Counter.most_common`` ties are insertion-ordered, which isn't
    stable across record orderings)."""
    if not counter:
        return None
    best = max(counter.items(), key=lambda kv: (kv[1], _neg_key(kv[0])))
    return best[0]


def _neg_key(s: str) -> tuple[int, ...]:
    """A sort key that orders strings *earlier-is-bigger* so ``max`` picks the
    alphabetically-first key on a count tie."""
    return tuple(-ord(c) for c in s)


# --------------------------------------------------------------------------- #
# pure core: streaks
# --------------------------------------------------------------------------- #
def streaks(active_dates: list[str], *, today: str | None = None) -> tuple[int, int]:
    """``(current, longest)`` consecutive-active-day streaks from a list of active
    ``YYYY-MM-DD`` days. Pure — *you* supply ``today`` (nothing is read from the
    clock), so it's deterministic.

    * **longest** is the longest run of consecutive calendar days anywhere in the
      set.
    * **current** is the run ending *today* — or ending *yesterday* (a streak you
      haven't broken yet because today isn't over). Any older gap → current is 0.
      If ``today`` is omitted, ``current`` is the run ending on the latest active
      day (handy for tests / static snapshots).

    Malformed dates are ignored. Duplicates collapse. An empty set → ``(0, 0)``.
    """
    days = sorted({d for d in (_as_date(s) for s in active_dates) if d is not None})
    if not days:
        return 0, 0

    # longest run anywhere
    longest = run = 1
    for prev, cur in zip(days, days[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)

    # current run, walking backwards from the most recent active day
    current = 1
    for prev, cur in zip(reversed(days), reversed(days[:-1])):
        # prev is the later day, cur the earlier one (we reversed)
        if (prev - cur).days == 1:
            current += 1
        else:
            break

    # anchor "current" to today: only counts if the latest active day is today
    # or yesterday, otherwise the streak is already broken.
    t = _as_date(today) if today else None
    if t is not None:
        gap = (t - days[-1]).days
        if gap > 1 or gap < 0:
            current = 0
    return current, longest


# --------------------------------------------------------------------------- #
# pure core: peak hour
# --------------------------------------------------------------------------- #
def peak_hour(hours: list[int]) -> int:
    """The most frequent hour-of-day (0-23) in ``hours``. Pure. Ties broken by the
    *earlier* hour for determinism. Empty input → ``0`` (midnight, a sane default
    the renderer treats as "no data")."""
    counts: Counter[int] = Counter(h for h in hours if isinstance(h, int) and 0 <= h <= 23)
    if not counts:
        return 0
    # max count, tie → smallest hour
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


# --------------------------------------------------------------------------- #
# pure core: contribution heatmap
# --------------------------------------------------------------------------- #
def heatmap_cells(
    date_counts: dict[str, int], *, today: str, weeks: int = 20
) -> list[list[int]]:
    """A GitHub-style contribution grid: ``weeks`` columns × 7 weekday rows of
    intensity levels ``0-4``, bucketed from per-day counts. Pure.

    The grid ends on the week containing ``today`` (its rightmost column), runs
    ``weeks`` columns back, and each column is a Sun→Sat week (row 0 = Sunday).
    Days outside the recorded range, and the future cells after ``today`` in the
    final column, are level 0.

    Buckets (matching the GitHub feel): 0 → no activity; then the positive counts
    are split into four quartile-ish bands so a light day and a heavy day look
    different. The busiest day(s) always land in level 4.
    """
    weeks = max(int(weeks), 1)
    t = _as_date(today) or date.today()
    # rightmost column is the Sun..Sat week containing today.
    # Python weekday(): Mon=0..Sun=6; we want Sun=0..Sat=6 → (wd + 1) % 7.
    sun_offset = (t.weekday() + 1) % 7
    last_sunday = t - timedelta(days=sun_offset)
    first_sunday = last_sunday - timedelta(weeks=weeks - 1)

    # clean the counts to real dates → ints
    clean: dict[date, int] = {}
    for k, v in (date_counts or {}).items():
        d = _as_date(k)
        if d is not None:
            clean[d] = clean.get(d, 0) + _safe_int(v)

    thresholds = _heat_thresholds([c for c in clean.values() if c > 0])

    grid: list[list[int]] = [[0] * weeks for _ in range(7)]
    for col in range(weeks):
        week_start = first_sunday + timedelta(weeks=col)
        for row in range(7):
            day = week_start + timedelta(days=row)
            if day > t:
                continue  # future — leave at 0
            grid[row][col] = _bucket(clean.get(day, 0), thresholds)
    return grid


def _heat_thresholds(positive_counts: list[int]) -> tuple[int, int, int]:
    """Three ascending cut points that split positive day-counts into levels
    1-4. Returns ``(t1, t2, t3)`` where a count ``c`` maps to level 1 if
    ``c<=t1`` … level 4 if ``c>t3``. Quantile-based, with sensible fallbacks for
    tiny / uniform data so distinct counts still spread across the bands."""
    if not positive_counts:
        return (1, 2, 3)
    hi = max(positive_counts)
    if hi <= 1:
        # everything is a single hit → all level 4 (a day is a day).
        return (0, 0, 0)
    if hi <= 4:
        # small range: one count per band up to the max.
        return (1, 2, 3)
    s = sorted(positive_counts)

    def q(frac: float) -> int:
        idx = min(int(frac * (len(s) - 1) + 0.5), len(s) - 1)
        return s[idx]

    t1, t2, t3 = q(0.25), q(0.50), q(0.75)
    # ensure strictly increasing so every band is reachable
    t2 = max(t2, t1 + 1)
    t3 = max(t3, t2 + 1)
    # never let the top band start at/above the max, or nothing is level 4
    t3 = min(t3, hi - 1)
    t2 = min(t2, t3 - 1) if t3 - 1 >= t1 + 1 else t2
    t1 = min(t1, max(t2 - 1, 1))
    return (t1, t2, t3)


def _bucket(count: int, thresholds: tuple[int, int, int]) -> int:
    """Map a day's count to an intensity level 0-4 given the three cut points."""
    if count <= 0:
        return 0
    t1, t2, t3 = thresholds
    if count <= t1:
        return 1
    if count <= t2:
        return 2
    if count <= t3:
        return 3
    return 4


# --------------------------------------------------------------------------- #
# pure core: the playful comparison line
# --------------------------------------------------------------------------- #
# Rough token counts of well-known texts (≈ words × 1.3). Ordered small→large;
# we pick the largest reference the user has comfortably *exceeded* so the
# multiple reads naturally ("≈ 3x War and Peace"), falling back to the smallest
# when they haven't passed even that yet.
_CORPORA: tuple[tuple[str, int], ...] = (
    ("a tweet", 50),
    ("a blog post", 1_200),
    ("a short story", 10_000),
    ("Animal Farm", 40_000),
    ("The Great Gatsby", 62_000),
    ("The Hobbit", 130_000),
    ("the Bible", 800_000),
    ("War and Peace", 780_000),
    ("the Harry Potter series", 1_500_000),
    ("the Lord of the Rings trilogy", 2_100_000),
    ("the entire English Wikipedia", 3_000_000_000),
)


def fun_comparison(total_tokens: int) -> str:
    """A playful one-liner sizing ``total_tokens`` against a famous text, e.g.
    ``"≈ 3.2x the tokens in Animal Farm"``. Pure.

    Picks the *largest* reference the total comfortably exceeds (so the multiple
    is ≥ ~1) and reports the multiple; for a total below the smallest reference
    it phrases it as a fraction of that smallest one. Zero/negative → an honest
    empty-state nudge.
    """
    total = max(int(total_tokens), 0)
    if total <= 0:
        return "no tokens yet — your story's still unwritten ✍"

    refs = sorted(_CORPORA, key=lambda kv: kv[1])
    # largest reference we've exceeded (>= it)
    chosen = None
    for name, size in refs:
        if total >= size:
            chosen = (name, size)
        else:
            break

    if chosen is None:
        # below even the smallest reference — phrase as a fraction
        name, size = refs[0]
        frac = total / size
        return f"≈ {frac:.0%} of the tokens in {name} — just getting warmed up 🌱"

    name, size = chosen
    mult = total / size
    return f"≈ {_fmt_mult(mult)} the tokens in {name} 📚"


def _fmt_mult(mult: float) -> str:
    """Format a multiple compactly: 1.5x, 12x, 3,400x, 2.1Mx …"""
    if mult >= 1_000_000:
        return f"{mult / 1_000_000:.1f}Mx"
    if mult >= 1_000:
        return f"{mult / 1_000:.1f}Kx"
    if mult >= 100:
        return f"{mult:,.0f}x"
    if mult >= 10:
        return f"{mult:.0f}x"
    return f"{mult:.1f}x"


# --------------------------------------------------------------------------- #
# impure thin layer: load the on-disk records
# --------------------------------------------------------------------------- #
def load_records(
    *, usage_path: Path | None = None, root: Path | str | None = None
) -> tuple[list[dict], list[dict]]:
    """Read the two on-disk sources the dashboard aggregates and return
    ``(usage_records, session_records)``. Impure (touches disk), but tolerant of
    everything: missing files, empty files, half-written JSON lines — it never
    raises, returning whatever it could parse (possibly two empty lists).

    * ``usage_records`` are the per-call token rows from ``.ronin/usage.jsonl``
      (via :mod:`ronin_cli.usage`), each a plain dict.
    * ``session_records`` are lightweight **metadata only** for archived sessions
      under ``.ronin/sessions/`` — ``{id, date, turns, updated}``. We read the
      transcript length to count turns/messages but never read its *content*.
    """
    usage_records = _load_usage(usage_path)
    session_records = _load_sessions(root)
    return usage_records, session_records


def _load_usage(usage_path: Path | None) -> list[dict]:
    """Read ``.ronin/usage.jsonl`` line-by-line into plain dicts. Parsed
    per-line on purpose (not via ``usage.load_records``, which is all-or-nothing
    and raises on a single bad row) so one half-written line can't discard the
    whole history — a dashboard must survive partial data."""
    import json

    out: list[dict] = []
    try:
        path = usage_path
        if path is None:
            from .usage import usage_path as _default_path  # type: ignore

            path = _default_path()
        path = Path(path)
        if not path.is_file():
            return out
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # half-written / corrupt line — skip it
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:  # unreadable file — honest empty, never crash
        return out
    return out


def _load_sessions(root: Path | str | None) -> list[dict]:
    """Session metadata only — id, day, turn/message counts. Never reads transcript
    *content* (privacy): we only take its length to count messages."""
    out: list[dict] = []
    try:
        from .sessions import list_sessions  # uses .ronin/sessions

        for meta in list_sessions(root):
            updated = str(meta.get("updated") or "")
            day = updated[:10] if len(updated) >= 10 else None
            turns = _safe_int(meta.get("turns"))
            out.append(
                {
                    "id": meta.get("id", ""),
                    "date": day,
                    "updated": updated,
                    "turns": turns,
                    # messages ≈ user turns + assistant replies; turns counts user
                    # messages, so a turn implies (at least) a reply → ~2x.
                    "messages": turns * 2 if turns else 0,
                }
            )
    except Exception:  # noqa: BLE001
        return out
    return out


def summarize(
    usage_records: list[dict], session_records: list[dict], *, today: str
) -> dict:
    """Compose the pure aggregations into one snapshot the renderer draws from.
    Pure given ``today``. Combines usage-derived figures (tokens, models, peak
    hour, activity days) with session-derived figures (sessions, messages)."""
    agg = aggregate(usage_records)

    # session figures (count + messages), plus their active days fold into the
    # activity calendar so a session with no token rows still lights a day up.
    sessions = len(session_records)
    messages = sum(_safe_int(s.get("messages")) for s in session_records)
    session_days = Counter()
    for s in session_records:
        d = s.get("date")
        if d and _as_date(str(d)):
            session_days[str(d)[:10]] += 1

    # merge activity calendars (usage rows + session days)
    date_counts = Counter(agg["date_counts"])
    date_counts.update(session_days)
    active_dates = sorted(date_counts)

    cur_streak, long_streak = streaks(active_dates, today=today)

    return {
        **agg,
        "sessions": sessions,
        "messages": messages,
        "date_counts": dict(date_counts),
        "active_dates": active_dates,
        "active_days": len(active_dates),
        "current_streak": cur_streak,
        "longest_streak": long_streak,
        "peak_hour": peak_hour(agg["hours"]),
        "has_data": bool(active_dates or agg["records"] or sessions),
        "today": today,
    }


# --------------------------------------------------------------------------- #
# impure thin layer: the dashboard
# --------------------------------------------------------------------------- #
def _hour_label(hour: int) -> str:
    """A friendly 12-hour clock label for an hour-of-day, e.g. 0→'12 AM', 20→'8 PM'."""
    ampm = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    return f"{h12} {ampm}"


def _fmt_int(n: int) -> str:
    """Human token/number formatting: 4 → '4', 12_345 → '12.3K', 6_400_000 → '6.4M'."""
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def _stat_card(label: str, value: str, *, accent: str = TEAL, sub: str = ""):
    """One headline stat as a small rounded panel — big value, muted label."""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    body = Table.grid(padding=0)
    body.add_column(justify="center")
    body.add_row(Text(value, style=f"bold {accent}"))
    body.add_row(Text(label.upper(), style=MUTE))
    if sub:
        body.add_row(Text(sub, style=SOFT))
    return Panel(body, border_style=accent, padding=(0, 1))


def _heatmap_renderable(snapshot: dict):
    """The contribution heatmap (weeks × 7 weekdays) as a Rich renderable, plus a
    Less→More legend. Teal-intensity squares; glyphs carry the level on
    low-colour terminals."""
    from rich.table import Table
    from rich.text import Text

    today = snapshot.get("today") or date.today().isoformat()
    weeks = 20
    grid = heatmap_cells(snapshot.get("date_counts", {}), today=today, weeks=weeks)

    block = Text()
    weekday_labels = ["", "Mon", "", "Wed", "", "Fri", ""]  # Sun=0 row blank-ish
    for row in range(7):
        block.append(f"{weekday_labels[row]:>3} ", style=MUTE)
        for col in range(weeks):
            level = grid[row][col]
            block.append(HEAT_GLYPHS[level] + " ", style=HEAT_COLORS[level])
        block.append("\n")

    # legend
    legend = Text("    less ", style=MUTE)
    for lvl in range(5):
        legend.append(HEAT_GLYPHS[lvl] + " ", style=HEAT_COLORS[lvl])
    legend.append("more", style=MUTE)
    block.append(legend)

    g = Table.grid()
    g.add_column()
    g.add_row(Text("Activity", style=f"bold {SKY}"))
    g.add_row(block)
    return g


def _panda_strip():
    """A small red-panda kaomoji + ronin wordmark, for a little delight."""
    from rich.table import Table
    from rich.text import Text

    from .theme import gradient_text

    try:
        from .panda_art import PANDA_NEUTRAL

        face = PANDA_NEUTRAL[0].strip()
    except Exception:  # noqa: BLE001
        face = "ʕ•ᴥ•ʔ"

    wordmark = gradient_text("ronin")
    line = Text()
    line.append(f"{face} ", style=f"bold {WARN}")  # warm amber — red-panda vibe
    g = Table.grid(padding=(0, 1))
    g.add_column()
    g.add_column()
    g.add_row(line, wordmark)
    return g


def render(console, *, records: dict | None = None) -> None:
    """Draw the ronin usage dashboard. Impure — when ``records`` is omitted it
    loads the on-disk usage log + session archive itself (via ``load_records``)
    and aggregates for *today*; pass a ready ``summarize(...)`` snapshot to render
    a known one (tests do this).

    Empty state is friendly, never zeros-as-failure: with nothing logged we show
    the panda and a nudge to go run ``ronin code``.
    """
    from rich.align import Align
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.text import Text

    from .theme import gradient_text

    if records is None:
        usage_records, session_records = load_records()
        snapshot = summarize(usage_records, session_records, today=date.today().isoformat())
    elif "has_data" in records:
        snapshot = records                      # already a summarize() snapshot
    else:
        # a raw {usage:[...], sessions:[...]} bundle (or just usage list)
        snapshot = summarize(
            records.get("usage", []) if isinstance(records, dict) else records,
            records.get("sessions", []) if isinstance(records, dict) else [],
            today=date.today().isoformat(),
        )

    # ----- header --------------------------------------------------------- #
    head = gradient_text("ronin stats")
    head.append("   your terminal, by the numbers", style=f"bold {MUTE}")
    console.print()
    console.print(head)
    console.print()

    # ----- empty state ---------------------------------------------------- #
    if not snapshot.get("has_data"):
        empty = Text()
        try:
            from .panda_art import PANDA_NEUTRAL

            for ln in PANDA_NEUTRAL:
                empty.append(ln + "\n", style=f"bold {WARN}")
        except Exception:  # noqa: BLE001
            empty.append("ʕ•ᴥ•ʔ\n", style=f"bold {WARN}")
        empty.append("\nno activity logged yet — go run ", style=SOFT)
        empty.append("ronin code", style=f"bold {TEAL}")
        empty.append("!", style=SOFT)
        empty.append("\nevery turn you take fills in this dashboard.", style=MUTE)
        console.print(
            Panel(Align.center(empty), border_style=TEAL, padding=(1, 4), title=f"[{TEAL}]welcome[/{TEAL}]")
        )
        console.print()
        return

    # ----- stat cards ----------------------------------------------------- #
    fav_model = snapshot.get("favorite_model") or "—"
    fav_model_short = fav_model.split("/")[-1]  # trim "accounts/.../model"
    peak = snapshot.get("peak_hour", 0)
    peak_str = _hour_label(peak) if snapshot.get("hours") else "—"

    cards = [
        _stat_card("Sessions", _fmt_int(snapshot.get("sessions", 0)), accent=TEAL),
        _stat_card("Messages", _fmt_int(snapshot.get("messages", 0)), accent=CYAN),
        _stat_card("Total tokens", _fmt_int(snapshot.get("total_tokens", 0)), accent=GOOD),
        _stat_card("Active days", _fmt_int(snapshot.get("active_days", 0)), accent=SKY),
        _stat_card("Current streak", f"{snapshot.get('current_streak', 0)}d", accent=WARN,
                   sub="🔥" if snapshot.get("current_streak", 0) > 0 else ""),
        _stat_card("Longest streak", f"{snapshot.get('longest_streak', 0)}d", accent=WARN),
        _stat_card("Peak hour", peak_str, accent=SKY),
        _stat_card("Favorite model", fav_model_short, accent=GOOD),
    ]
    console.print(Columns(cards, equal=True, expand=True))
    console.print()

    # ----- heatmap -------------------------------------------------------- #
    console.print(
        Panel(_heatmap_renderable(snapshot), border_style=TEAL, padding=(1, 2),
              title=f"[{TEAL}]contribution heatmap[/{TEAL}]",
              subtitle=f"[{MUTE}]last 20 weeks[/{MUTE}]")
    )
    console.print()

    # ----- fun line + panda wordmark -------------------------------------- #
    fun = Text()
    fun.append("💡 ", style=WARN)
    fun.append("you've written ", style=SOFT)
    fun.append(_fmt_int(snapshot.get("total_tokens", 0)) + " tokens", style=f"bold {TEAL}")
    fun.append("  ", style=MUTE)
    fun.append(fun_comparison(snapshot.get("total_tokens", 0)), style=f"italic {SKY}")

    foot = Columns([fun, Align.right(_panda_strip())], expand=True)
    console.print(Panel(foot, border_style=MUTE, padding=(0, 2)))
    console.print()


# --------------------------------------------------------------------------- #
# entry point: `ronin stats`
# --------------------------------------------------------------------------- #
def run(console=None) -> None:
    """The ``ronin stats`` command body: load on-disk usage + sessions and draw the
    dashboard. Read-only — it never writes, and never reads message content.

    main.py wires this with a thin Typer command (kept out of this module so the
    constraint "only edit stats.py" holds)::

        @app.command()
        def stats() -> None:
            \"\"\"A usage dashboard: sessions, tokens, streaks, and an activity heatmap.\"\"\"
            from .stats import run as run_stats
            run_stats(console)
    """
    if console is None:
        from rich.console import Console

        console = Console()
    render(console)
