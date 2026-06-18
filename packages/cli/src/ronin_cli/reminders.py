"""Bot-native reminders and recurring schedules that push to Telegram.

The Telegram bridge already long-polls api.telegram.org, knows the owner's chat
id, and has the bot token. That is everything a reminder needs: no new daemon,
no new credentials, no cron line. This module is the missing store plus a tiny
stdlib time parser so the bot can recognize "remind me ..." messages, persist
them, and fire the due ones on its existing poll tick.

What lives here:

  * a durable store at ``~/.ronin/reminders.json`` (a list of reminder dicts),
    with add / list / remove helpers,
  * ``parse_reminder`` - a self-contained, stdlib-only parser that turns
    "remind me at 6pm to X", "remind me in 30 minutes to X", "remind me every
    day at 8am to X", "briefing every morning at 8am: X", "list reminders" and
    "cancel reminder N" into a small intent object, and
  * ``due_reminders`` / ``reschedule`` - pure helpers the poll loop uses to find
    what is due now and advance recurring ones.

It is deliberately offline and side-effect-light. Parsing is pure. The store is
a single JSON file. Nothing here sleeps or touches the network; the Telegram bot
drives it from its own loop and does the actual sending.

The existing ``scheduler.py`` is a cron-driven store you trigger from the system
crontab. This is the complement: a self-contained reminder that the always-on
bot fires itself. They do not overlap, so this does not duplicate it.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Recurrence intervals we support. ``none`` is a one-shot. The values are the
# stored ``repeat`` strings; everything else is treated as a one-shot.
REPEAT_SECONDS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 7 * 86400,
}

# Map the words a user might type for a recurrence onto our canonical repeat.
_REPEAT_WORDS = {
    "hour": "hourly",
    "hourly": "hourly",
    "day": "daily",
    "daily": "daily",
    "morning": "daily",  # "every morning" == daily (at the given time)
    "week": "weekly",
    "weekly": "weekly",
}


def _reminders_path() -> Path:
    return Path.home() / ".ronin" / "reminders.json"


# --------------------------------------------------------------------------
# Persistence. One JSON file; a list of reminder dicts.
# --------------------------------------------------------------------------


@dataclass
class Reminder:
    id: int
    when_epoch: float
    text: str
    chat_id: int
    repeat: str = "none"  # none | hourly | daily | weekly
    done: bool = False
    is_briefing: bool = False  # if True, run text through the agent at fire time

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "when_epoch": self.when_epoch,
            "text": self.text,
            "chat_id": self.chat_id,
            "repeat": self.repeat,
            "done": self.done,
            "is_briefing": self.is_briefing,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Reminder":
        repeat = str(d.get("repeat", "none"))
        if repeat not in REPEAT_SECONDS:
            repeat = "none"
        return cls(
            id=int(d.get("id", 0)),
            when_epoch=float(d.get("when_epoch", 0.0)),
            text=str(d.get("text", "")),
            chat_id=int(d.get("chat_id", 0)),
            repeat=repeat,
            done=bool(d.get("done", False)),
            is_briefing=bool(d.get("is_briefing", False)),
        )


def load_reminders() -> list[Reminder]:
    """Load all reminders. Returns [] on a missing or unreadable file."""
    p = _reminders_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [Reminder.from_dict(r) for r in data.get("reminders", [])]
    except (OSError, ValueError):
        return []


def _save(reminders: list[Reminder]) -> None:
    p = _reminders_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(
            json.dumps({"reminders": [r.to_dict() for r in reminders]}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _next_id(reminders: list[Reminder]) -> int:
    """Stable, monotonically increasing ids so 'cancel reminder N' stays sane."""
    return (max((r.id for r in reminders), default=0)) + 1


def add_reminder(
    when_epoch: float,
    text: str,
    chat_id: int,
    *,
    repeat: str = "none",
    is_briefing: bool = False,
) -> Reminder:
    """Persist a new reminder and return it (with its assigned id)."""
    if repeat not in REPEAT_SECONDS:
        repeat = "none"
    reminders = load_reminders()
    reminder = Reminder(
        id=_next_id(reminders),
        when_epoch=float(when_epoch),
        text=text.strip(),
        chat_id=int(chat_id),
        repeat=repeat,
        is_briefing=is_briefing,
    )
    reminders.append(reminder)
    _save(reminders)
    return reminder


def list_reminders(chat_id: int | None = None, *, include_done: bool = False) -> list[Reminder]:
    """List reminders, newest-first by fire time. Filters to a chat if given and,
    by default, hides ones already marked done (one-shots that fired)."""
    out = load_reminders()
    if chat_id is not None:
        out = [r for r in out if r.chat_id == chat_id]
    if not include_done:
        out = [r for r in out if not r.done]
    return sorted(out, key=lambda r: r.when_epoch)


def remove_reminder(reminder_id: int) -> bool:
    """Delete a reminder by id. Returns True if one was removed."""
    reminders = load_reminders()
    kept = [r for r in reminders if r.id != reminder_id]
    if len(kept) == len(reminders):
        return False
    _save(kept)
    return True


def due_reminders(reminders: list[Reminder], now_epoch: float) -> list[Reminder]:
    """The subset due to fire at or before ``now_epoch`` (pure, no side effects).
    Done one-shots never fire again."""
    return [r for r in reminders if not r.done and r.when_epoch <= now_epoch]


def mark_fired(reminder_id: int, now_epoch: float) -> Reminder | None:
    """Advance a reminder after it fired and persist the change.

    A recurring reminder is rescheduled to its next slot strictly after
    ``now_epoch`` (so a late tick cannot make it double-fire). A one-shot is
    marked done. Returns the updated reminder, or None if the id is unknown.
    """
    reminders = load_reminders()
    updated: Reminder | None = None
    for r in reminders:
        if r.id != reminder_id:
            continue
        if r.repeat in REPEAT_SECONDS:
            r.when_epoch = _next_slot(r.when_epoch, r.repeat, now_epoch)
        else:
            r.done = True
        updated = r
        break
    if updated is not None:
        _save(reminders)
    return updated


def _next_slot(when_epoch: float, repeat: str, now_epoch: float) -> float:
    """First recurrence slot strictly after ``now_epoch`` for a fixed interval.
    Stepping by whole intervals keeps the time-of-day stable for daily/weekly."""
    step = REPEAT_SECONDS[repeat]
    nxt = when_epoch + step
    while nxt <= now_epoch:
        nxt += step
    return nxt


# --------------------------------------------------------------------------
# Parsing. Self-contained, stdlib only. No NLP, no extra deps.
# --------------------------------------------------------------------------


@dataclass
class ParsedReminder:
    """The result of parsing a message.

    ``kind`` is one of:
      * "add"    -> a reminder to store (when_epoch / repeat / text / is_briefing set)
      * "list"   -> the user asked to list reminders
      * "cancel" -> the user asked to cancel one (cancel_id set)
      * "error"  -> looked like a reminder but the time could not be parsed
                    (``error`` holds a short rephrase hint)
      * None     -> not a reminder message at all (let the agent handle it)
    """

    kind: str | None = None
    when_epoch: float | None = None
    repeat: str = "none"
    text: str = ""
    is_briefing: bool = False
    cancel_id: int | None = None
    error: str | None = None


_REPHRASE = (
    "I could not read that time. Try 'remind me at 6pm to call mom', "
    "'remind me in 30 minutes to stretch', or 'remind me every day at 8am to journal'."
)

_UNSUPPORTED_REPEAT = (
    "I can only repeat hourly, daily, or weekly. Try 'remind me every day at "
    "9am to pay rent' or set a one-shot like 'remind me at 9am to pay rent'."
)


def _unsupported_every(sched: str) -> bool:
    """True if the phrase asks to repeat 'every <word>' but <word> is not a
    recurrence we support (e.g. month, weekday, year, fortnight, quarter).

    Without this, an unsupported recurrence would silently fall through to a
    one-shot: the user asked for a repeating reminder and got one that fires
    once and never again, with no signal that the recurrence was dropped.
    """
    m = re.search(r"\bevery\s+([a-z]+)", sched.lower())
    return bool(m) and _REPEAT_WORDS.get(m.group(1), "none") == "none"


def parse_reminder(message: str, *, now: datetime | None = None) -> ParsedReminder | None:
    """Parse a message into a reminder intent, or None if it is not one.

    Recognizes, case-insensitively:
      * "list reminders" / "reminders"            -> kind="list"
      * "cancel reminder 3" / "delete reminder 3" -> kind="cancel", cancel_id=3
      * "briefing every morning at 8am: <prompt>" -> kind="add", is_briefing=True
      * "remind me in 30 minutes to X"            -> kind="add", one-shot
      * "remind me at 6pm to X"                   -> kind="add", one-shot
      * "remind me every day at 8am to X"         -> kind="add", recurring

    On a recognized-but-unparseable time it returns kind="error" with a hint, so
    the bot can ask the user to rephrase instead of silently running the agent.
    Pure: ``now`` is injectable so tests need no real clock.
    """
    now = now or datetime.now()
    raw = (message or "").strip()
    low = raw.lower()

    if not raw:
        return None

    # --- list ---------------------------------------------------------------
    if low in {"list reminders", "reminders", "list reminder", "show reminders"}:
        return ParsedReminder(kind="list")

    # --- cancel reminder N --------------------------------------------------
    m = re.match(r"^(?:cancel|delete|remove)\s+reminder\s+#?(\d+)\b", low)
    if m:
        return ParsedReminder(kind="cancel", cancel_id=int(m.group(1)))

    # --- daily briefing: "briefing every ... : <prompt>" --------------------
    if low.startswith("briefing"):
        return _parse_briefing(raw, now)

    # --- remind me ... ------------------------------------------------------
    if low.startswith("remind me") or low.startswith("remind "):
        return _parse_remind(raw, now)

    return None


def _strip_to_text(body: str) -> str:
    """Pull the reminder text out of the trailing 'to <text>' or ': <text>'."""
    m = re.search(r"(?:\bto\b|:)\s+(.*)$", body, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return body.strip()


def _parse_briefing(raw: str, now: datetime) -> ParsedReminder:
    """Parse 'briefing every morning at 8am: <prompt>' into a recurring add whose
    text is meant to be run through the agent at fire time."""
    # Everything after 'briefing' is the schedule + ': prompt'.
    body = raw[len("briefing"):].strip()
    prompt = ""
    # The prompt is after the first colon; the schedule words come before it.
    if ":" in body:
        sched, _, prompt = body.partition(":")
    else:
        sched = body
    prompt = prompt.strip()
    if not prompt:
        return ParsedReminder(
            kind="error",
            error="Briefings need a prompt. Try 'briefing every morning at 8am: summary of my day'.",
        )
    if _unsupported_every(sched):
        return ParsedReminder(kind="error", error=_UNSUPPORTED_REPEAT)
    when, repeat = _parse_when(sched, now)
    if when is None:
        return ParsedReminder(kind="error", error=_REPHRASE)
    return ParsedReminder(
        kind="add", when_epoch=when, repeat=repeat, text=prompt, is_briefing=True
    )


def _parse_remind(raw: str, now: datetime) -> ParsedReminder:
    """Parse 'remind me [every ...] [at/in ...] to <text>'."""
    text = _strip_to_text(raw)
    if not text or text.lower().startswith("remind"):
        return ParsedReminder(
            kind="error",
            error="What should I remind you about? Try 'remind me at 6pm to call mom'.",
        )
    # The schedule is the part before the 'to <text>' tail.
    sched = raw
    m = re.search(r"(?:\bto\b|:)\s+", raw, flags=re.IGNORECASE)
    if m:
        sched = raw[: m.start()]
    if _unsupported_every(sched):
        return ParsedReminder(kind="error", error=_UNSUPPORTED_REPEAT)
    when, repeat = _parse_when(sched, now)
    if when is None:
        return ParsedReminder(kind="error", error=_REPHRASE)
    return ParsedReminder(kind="add", when_epoch=when, repeat=repeat, text=text)


def _parse_when(sched: str, now: datetime) -> tuple[float | None, str]:
    """Turn a schedule phrase into (when_epoch, repeat).

    Handles 'in <n> <unit>', 'at <time>', and 'every <interval> [at <time>]'.
    Returns (None, "none") if no time could be found.
    """
    low = sched.lower()

    repeat = "none"
    every = re.search(r"\bevery\s+([a-z]+)", low)
    if every:
        repeat = _REPEAT_WORDS.get(every.group(1), "none")

    # "in <n> <unit>" -> relative one-shot. A relative offset never recurs.
    rel = _parse_relative(low, now)
    if rel is not None:
        return rel.timestamp(), "none"

    # "at <time>" (optionally with an 'every' giving the recurrence).
    at = _parse_at_time(low, now)
    if at is not None:
        # A bare "every day" with no "at" still means daily at the current
        # minute; but if we found a clock time, fire at that time.
        if repeat == "none":
            repeat = _repeat_from_every(low)
        return at.timestamp(), repeat

    # "every day/hour/week" with no explicit clock time -> next interval from now.
    if repeat != "none":
        first = now + timedelta(seconds=REPEAT_SECONDS[repeat])
        return first.timestamp(), repeat

    return None, "none"


def _repeat_from_every(low: str) -> str:
    every = re.search(r"\bevery\s+([a-z]+)", low)
    if every:
        return _REPEAT_WORDS.get(every.group(1), "none")
    return "none"


def _parse_relative(low: str, now: datetime) -> datetime | None:
    """Parse 'in <n> <unit>' (minutes/hours/days/seconds/weeks). None if absent."""
    m = re.search(
        r"\bin\s+(\d+)\s*(second|sec|minute|min|hour|hr|day|week)s?\b", low
    )
    if not m:
        return None
    amount = int(m.group(1))
    unit = m.group(2)
    seconds = {
        "second": 1, "sec": 1,
        "minute": 60, "min": 60,
        "hour": 3600, "hr": 3600,
        "day": 86400,
        "week": 7 * 86400,
    }[unit]
    return now + timedelta(seconds=amount * seconds)


def _parse_at_time(low: str, now: datetime) -> datetime | None:
    """Parse 'at <time>' into the next occurrence of that clock time.

    Accepts '6pm', '6:30pm', '8am', '18:00', '08:30', and the words 'noon' /
    'midnight'. If the time has already passed today the next occurrence is
    tomorrow. Returns None if no clock time is present.
    """
    if re.search(r"\bat\s+noon\b", low) or low.strip() == "noon":
        return _next_occurrence(now, 12, 0)
    if re.search(r"\bat\s+midnight\b", low) or low.strip() == "midnight":
        return _next_occurrence(now, 0, 0)

    # 12-hour with am/pm: 6pm, 6:30 pm, 12:05am
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
    if not m:
        # bare am/pm time without "at" (e.g. "every day 8am")
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3) == "pm":
            hour += 12
        minute = int(m.group(2) or 0)
        if hour > 23 or minute > 59:
            return None
        return _next_occurrence(now, hour, minute)

    # 24-hour: at 18:00, at 08:30 (require a colon so we don't grab "in 30").
    m = re.search(r"\bat\s+(\d{1,2}):(\d{2})\b", low)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if hour > 23 or minute > 59:
            return None
        return _next_occurrence(now, hour, minute)

    return None


def _next_occurrence(now: datetime, hour: int, minute: int) -> datetime:
    """The next datetime at hour:minute - today if still ahead, else tomorrow."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


# --------------------------------------------------------------------------
# Human-readable formatting for confirmations and the list reply.
# --------------------------------------------------------------------------


def format_when(when_epoch: float) -> str:
    """A short local-time string for a confirmation, e.g. 'Mon 18:00'."""
    return datetime.fromtimestamp(when_epoch).strftime("%a %H:%M")


def confirm_text(reminder: Reminder) -> str:
    """The confirmation line sent back when a reminder is stored."""
    when = format_when(reminder.when_epoch)
    repeat = "" if reminder.repeat == "none" else f", repeating {reminder.repeat}"
    kind = "briefing" if reminder.is_briefing else "reminder"
    return f"ok, {kind} #{reminder.id} set for {when}{repeat}: {reminder.text}"


def list_text(reminders: list[Reminder]) -> str:
    """Render a list of reminders for the chat, or a friendly empty message."""
    if not reminders:
        return "no reminders set. Try 'remind me at 6pm to call mom'."
    lines = ["your reminders:"]
    for r in reminders:
        when = format_when(r.when_epoch)
        repeat = "" if r.repeat == "none" else f" (every {r.repeat})"
        tag = "briefing: " if r.is_briefing else ""
        lines.append(f"#{r.id} {when}{repeat} - {tag}{r.text}")
    return "\n".join(lines)
