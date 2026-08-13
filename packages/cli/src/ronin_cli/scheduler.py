"""A real task scheduler for ronin - store tasks, list them, and run the due ones.

`ronin cron` only *explains* a cron expression; `cron_install` only upserts a
single line in the system crontab. Neither remembers a set of tasks or knows
which are due. This module is the missing piece: a small, durable scheduler that

  * stores named tasks (a prompt + a 5-field cron schedule) at
    ``~/.ronin/schedule.json``,
  * lists them with their next run time,
  * computes which tasks are *due* at a given instant (``due_tasks``), and
  * runs the due ones through ronin's own offline agent and records the result.

It is deliberately self-contained and offline: the cron matcher is pure (no
``croniter`` dependency), persistence is a single JSON file, and running a task
goes through the same ``run_ask`` path the CLI already uses - which falls back to
the offline demo brain when no API key is set, so ``run-due`` works at $0 with no
network. A real system crontab line (installed via ``cron_install``) can invoke
``ronin schedule run-due`` once a minute to make the whole thing fire on time,
but every piece here is testable without touching cron or the network.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

# A managed task name: short, kebab/underscore-friendly, so it reads cleanly in
# the table and can't collide with shell metacharacters when echoed.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,40}$")

# Field bounds for a standard 5-field cron expression (minute hour dom month dow).
# day-of-week is 0-6 with Sunday == 0 (7 is also accepted as Sunday, like cron).
_FIELD_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))

_ALIASES = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def _schedule_path() -> Path:
    return Path.home() / ".ronin" / "schedule.json"


# --------------------------------------------------------------------------
# Pure cron matching - no dependency, fully unit-testable.
# --------------------------------------------------------------------------

def _parse_field(spec: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field ('*', '*/5', '1-5', '1,3,5', or a mix) into the set
    of integers it matches within [lo, hi]. Raises ValueError on a bad field."""
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty cron field part in {spec!r}")
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"bad step in cron field {part!r}")
            step = int(step_s)
            part = base or "*"
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise ValueError(f"bad range in cron field {part!r}")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise ValueError(f"bad cron field {part!r}")
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron field {part!r} out of range [{lo},{hi}]")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> list[set[int]]:
    """Parse a 5-field cron expression (or @alias) into five matched-value sets.
    Pure. Raises ValueError if the expression is not five valid fields."""
    expr = _ALIASES.get(expr.strip(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("expected 5 cron fields or a @alias (e.g. '0 9 * * 1-5')")
    fields: list[set[int]] = []
    for spec, (lo, hi) in zip(parts, _FIELD_BOUNDS):
        s = _parse_field(spec, lo, hi)
        fields.append(s)
    return fields


def is_valid_cron(expr: str) -> bool:
    try:
        parse_cron(expr)
        return True
    except ValueError:
        return False


def _matches(fields: list[set[int]], when: datetime) -> bool:
    """True if ``when`` (minute resolution) satisfies the parsed cron fields.
    day-of-week: cron treats Sunday as 0; Python's weekday() is Mon=0..Sun=6."""
    minute, hour, dom, month, dow = fields
    py_dow = (when.weekday() + 1) % 7  # Mon=0..Sun=6  ->  Sun=0..Sat=6
    return (
        when.minute in minute
        and when.hour in hour
        and when.day in dom
        and when.month in month
        and py_dow in dow
    )


def next_run_after(expr: str, base: datetime) -> datetime | None:
    """The first minute strictly after ``base`` that matches ``expr``, or None if
    none is found within ~4 years (a guard against impossible dates like Feb 31).
    Pure."""
    try:
        fields = parse_cron(expr)
    except ValueError:
        return None
    # Step minute-by-minute from the next whole minute. 4 years of minutes is the
    # upper bound that still terminates for the rarest valid schedules.
    cur = (base + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(366 * 4 * 24 * 60):
        if _matches(fields, cur):
            return cur
        cur += timedelta(minutes=1)
    return None


# --------------------------------------------------------------------------
# Task model + persistence.
# --------------------------------------------------------------------------

@dataclass
class Task:
    name: str
    prompt: str
    cron: str
    created_at: float = field(default_factory=time.time)
    last_run: float | None = None  # epoch seconds of the last run, or None
    runs: int = 0
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "cron": self.cron,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "runs": self.runs,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(
            name=str(d.get("name", "")),
            prompt=str(d.get("prompt", "")),
            cron=str(d.get("cron", "")),
            created_at=float(d.get("created_at", time.time())),
            last_run=(float(d["last_run"]) if d.get("last_run") is not None else None),
            runs=int(d.get("runs", 0)),
            enabled=bool(d.get("enabled", True)),
        )


def load_tasks() -> list[Task]:
    p = _schedule_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [Task.from_dict(t) for t in data.get("tasks", [])]
    except (OSError, ValueError):
        return []


def _save(tasks: list[Task]) -> None:
    p = _schedule_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(
            json.dumps({"tasks": [t.to_dict() for t in tasks]}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def normalize_name(name: str) -> str:
    """Slugify a proposed task name to the form the store accepts."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:41]


def add_task(name: str, prompt: str, cron: str) -> Task:
    """Store a new task (or replace an existing one of the same name). Raises
    ValueError on a bad name, empty prompt, or invalid cron expression."""
    slug = normalize_name(name)
    if not _NAME_RE.match(slug):
        raise ValueError(f"invalid task name {name!r} - use letters, digits, '-' and '_'")
    if not prompt.strip():
        raise ValueError("task prompt is empty")
    if not is_valid_cron(cron):
        raise ValueError(f"invalid cron expression {cron!r} (expected 5 fields or a @alias)")
    tasks = [t for t in load_tasks() if t.name != slug]
    task = Task(name=slug, prompt=prompt.strip(), cron=cron.strip())
    tasks.append(task)
    _save(tasks)
    return task


def remove_task(name: str) -> bool:
    """Delete a task by name. Returns True if one was removed."""
    slug = normalize_name(name)
    tasks = load_tasks()
    kept = [t for t in tasks if t.name != slug]
    if len(kept) == len(tasks):
        return False
    _save(kept)
    return True


def get_task(name: str) -> Task | None:
    slug = normalize_name(name)
    for t in load_tasks():
        if t.name == slug:
            return t
    return None


def is_due(task: Task, now: datetime) -> bool:
    """True if ``task`` should run at minute ``now``: its cron matches this minute
    and it has not already run during this same minute (idempotent within the
    minute, so a per-minute cron tick won't double-fire). Pure."""
    if not task.enabled:
        return False
    try:
        fields = parse_cron(task.cron)
    except ValueError:
        return False
    if not _matches(fields, now):
        return False
    if task.last_run is not None:
        last = datetime.fromtimestamp(task.last_run).replace(second=0, microsecond=0)
        if last >= now.replace(second=0, microsecond=0):
            return False
    return True


def due_tasks(tasks: list[Task], now: datetime) -> list[Task]:
    """The subset of ``tasks`` due to run at ``now`` (pure - no side effects)."""
    return [t for t in tasks if is_due(t, now)]


def mark_ran(name: str, when: float | None = None) -> None:
    """Record that a task ran (bumps run count + last_run). Persists."""
    slug = normalize_name(name)
    tasks = load_tasks()
    for t in tasks:
        if t.name == slug:
            t.last_run = when if when is not None else time.time()
            t.runs += 1
            break
    _save(tasks)


# --------------------------------------------------------------------------
# Running due tasks through ronin's own agent (offline-capable).
# --------------------------------------------------------------------------

@dataclass
class RunResult:
    name: str
    prompt: str
    success: bool
    output: str
    error: str | None = None


def run_due(
    config: Any,
    now: datetime | None = None,
    *,
    runner: Callable[[Any, str], Any] | None = None,
) -> list[RunResult]:
    """Run every task due at ``now`` (default: the current minute) through ronin's
    agent, recording each as having run.

    ``runner`` defaults to ``ronin_cli.runner.run_ask``, which uses the configured
    provider and transparently falls back to the offline demo brain when there is
    no API key - so ``run_due`` works with no keys and no network. Inject a stub
    ``runner`` in tests to assert the wiring without invoking any model.
    """
    now = now or datetime.now()
    default_runner = runner is None

    results: list[RunResult] = []
    for task in due_tasks(load_tasks(), now):
        try:
            if default_runner:
                from .durable_runtime import surface_runtime
                from .runner import run_ask

                runtime = surface_runtime(Path.cwd(), "schedule", task.name)
                res = run_ask(
                    config, task.prompt, journal=runtime.journal,
                    journal_run_id=runtime.run_id, budget=runtime.budget,
                )
            else:
                assert runner is not None
                res = runner(config, task.prompt)
            results.append(RunResult(
                name=task.name,
                prompt=task.prompt,
                success=bool(getattr(res, "success", True)),
                output=str(getattr(res, "output", "")),
                error=getattr(res, "error", None),
            ))
        except Exception as exc:  # noqa: BLE001 - one bad task must not abort the rest
            results.append(RunResult(
                name=task.name, prompt=task.prompt, success=False,
                output="", error=f"{exc.__class__.__name__}: {exc}",
            ))
        # Mark as ran regardless of outcome so a persistently-failing task doesn't
        # re-fire every tick within the same minute.
        mark_ran(task.name, when=now.timestamp())
    return results
