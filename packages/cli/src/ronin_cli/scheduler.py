"""Scheduler — give the agent a goal and a cron schedule; run what's due.

A scheduled task is a *goal* (the question/instruction handed to the agent) plus
a 5-field *cron schedule*. Tasks live in ``.ronin/schedule.toml`` (project-local,
plaintext) exactly like saved queries, so they are easy to read, diff, and commit.

``run-due`` is a real runner: it walks the store, asks each task whether it is due
(its cron matched at or after ``last_run`` and at or before now), and for those
that are it invokes the agent through ``run_ask`` — the same path ``ronin ask``
uses — then records the run. There is no fake "it ran" flag: a task is only
marked run after the agent actually returns.

A long-running daemon is optional and intentionally NOT built in: the honest,
portable way to run this on a schedule is the OS scheduler (cron / launchd /
systemd-timer) calling ``ronin schedule run-due`` every minute. ``cron-line``
prints a ready-to-paste crontab line; see the README for the launchd/systemd
recipe. This keeps the tool a real runner, not a faked always-on process.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

import tomli_w
import tomllib
from pydantic import BaseModel, Field

from .cron_match import cron_matches, is_valid_cron, normalize

SCHEDULE_FILENAME = "schedule.toml"
_ISO = "%Y-%m-%dT%H:%M:%S"


def _now() -> datetime:
    """Wall-clock now, truncated to the minute (cron's resolution). Local time so
    a ``0 9 * * *`` task fires at the user's 09:00, not UTC's."""
    return datetime.now().replace(second=0, microsecond=0)


class ScheduledTask(BaseModel):
    """One scheduled job: a goal handed to the agent on a cron cadence."""

    name: str
    goal: str
    cron: str
    description: str = ""
    enabled: bool = True
    created: str = ""
    last_run: str | None = None  # ISO minute of the last successful run
    last_status: str | None = None  # "ok" | "error" — outcome of the last run
    run_count: int = 0

    def is_due(self, now: datetime, *, grace_minutes: int = 0) -> bool:
        """True iff this task should run at ``now`` and has not already run for
        the firing minute.

        We scan each minute in ``[now - grace_minutes, now]`` and fire if the cron
        matched any of them — so a missed window (the machine was asleep, run-due
        ran late) still triggers exactly one catch-up run rather than being
        silently skipped. ``grace_minutes`` is the catch-up reach: 0 means only
        the current minute counts (no catch-up). A minute at or before
        ``last_run`` never re-fires, so a task can't run twice for the same
        firing."""
        from datetime import timedelta

        if not self.enabled:
            return False
        if not is_valid_cron(self.cron):
            return False

        last_iso = self.last_run if self.last_run else None
        start = now - timedelta(minutes=max(0, grace_minutes))

        cursor = start
        while cursor <= now:
            if cron_matches(self.cron, cursor):
                # Don't re-fire a minute we already ran for (or earlier).
                if last_iso is not None and cursor.strftime(_ISO) <= last_iso:
                    cursor += timedelta(minutes=1)
                    continue
                return True
            cursor += timedelta(minutes=1)
        return False


class ScheduleStore(BaseModel):
    """The on-disk collection of scheduled tasks (one TOML file)."""

    tasks: dict[str, ScheduledTask] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "ScheduleStore":
        if not path.exists():
            return cls()
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        tasks = {
            name: ScheduledTask(name=name, **payload)
            for name, payload in raw.get("tasks", {}).items()
        }
        return cls(tasks=tasks)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        out: dict[str, dict] = {"tasks": {}}
        for name, t in self.tasks.items():
            payload = t.model_dump(exclude={"name"})
            # Drop empty/None so the TOML stays tidy and human-readable.
            payload = {k: v for k, v in payload.items() if v not in (None, "", 0, False) or k in ("enabled",)}
            payload["enabled"] = t.enabled  # always persist enabled explicitly
            out["tasks"][name] = payload
        with path.open("wb") as fh:
            tomli_w.dump(out, fh)

    def add(self, name: str, goal: str, cron: str, description: str = "") -> ScheduledTask:
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"invalid name {name!r} — use letters, digits, '-', '_' only")
        if not is_valid_cron(cron):
            raise ValueError(f"invalid cron {cron!r} — need 5 fields or a @alias (e.g. '@daily', '0 9 * * 1-5')")
        if name in self.tasks:
            raise ValueError(f"a task named {name!r} already exists — remove it first or pick another name")
        task = ScheduledTask(
            name=name,
            goal=goal,
            cron=normalize(cron),
            description=description,
            created=_now().strftime(_ISO),
        )
        self.tasks[name] = task
        return task

    def get(self, name: str) -> ScheduledTask:
        if name not in self.tasks:
            raise KeyError(f"no scheduled task named {name!r}")
        return self.tasks[name]

    def remove(self, name: str) -> bool:
        return self.tasks.pop(name, None) is not None

    def set_enabled(self, name: str, enabled: bool) -> ScheduledTask:
        task = self.get(name)
        task.enabled = enabled
        return task

    def list_all(self) -> list[ScheduledTask]:
        return sorted(self.tasks.values(), key=lambda t: t.name)

    def due(self, now: datetime | None = None, *, grace_minutes: int = 0) -> list[ScheduledTask]:
        now = now or _now()
        return [t for t in self.list_all() if t.is_due(now, grace_minutes=grace_minutes)]


class RunRecord(BaseModel):
    """The outcome of running one due task."""

    name: str
    status: str  # "ok" | "error"
    summary: str = ""
    error: str | None = None


def run_due(
    store: ScheduleStore,
    *,
    now: datetime | None = None,
    grace_minutes: int = 0,
    runner: Callable[[ScheduledTask], RunRecord],
    notifier: Callable[[str], None] | None = None,
) -> list[RunRecord]:
    """Execute every due task via ``runner``, record the outcome, and notify.

    Pure of I/O itself: the caller injects ``runner`` (which actually invokes the
    agent) and an optional ``notifier`` (which actually sends the notification),
    so this orchestration is fully unit-testable offline. ``store`` is mutated in
    place with each task's ``last_run`` / ``last_status`` / ``run_count``; the
    caller persists it. Returns one ``RunRecord`` per task that ran.
    """
    now = now or _now()
    records: list[RunRecord] = []
    for task in store.due(now, grace_minutes=grace_minutes):
        record = runner(task)
        task.last_run = now.strftime(_ISO)
        task.last_status = record.status
        task.run_count += 1
        records.append(record)
        if notifier is not None:
            if record.status == "ok":
                notifier(f"ronin schedule: '{task.name}' ran — {record.summary}".rstrip(" —"))
            else:
                notifier(f"ronin schedule: '{task.name}' FAILED — {record.error or 'unknown error'}")
    return records


def default_path() -> Path:
    return Path(".ronin") / SCHEDULE_FILENAME


def crontab_line(every_minute: bool = True, ronin_bin: str = "ronin") -> str:
    """A ready-to-paste crontab line that runs the due tasks each minute.

    The OS scheduler is the honest "daemon": this just hands the user the line.
    """
    schedule = "* * * * *" if every_minute else "*/5 * * * *"
    return f"{schedule} {ronin_bin} schedule run-due >> ~/.ronin/schedule.log 2>&1"
