"""Tests for the scheduler store and runner (pure/offline, no agent calls)."""
from __future__ import annotations

from datetime import datetime

import pytest

from ronin_cli.scheduler import (
    RunRecord,
    ScheduledTask,
    ScheduleStore,
    crontab_line,
    run_due,
)


def test_add_and_get() -> None:
    store = ScheduleStore()
    task = store.add("nightly", "summarize the day", "@daily", description="dd")
    assert task.cron == "0 0 * * *"           # alias normalized
    assert task.created                       # stamped
    assert store.get("nightly").goal == "summarize the day"


def test_add_rejects_bad_name() -> None:
    store = ScheduleStore()
    with pytest.raises(ValueError):
        store.add("bad name!", "g", "@daily")


def test_add_rejects_bad_cron() -> None:
    store = ScheduleStore()
    with pytest.raises(ValueError):
        store.add("t", "g", "not a cron")


def test_add_rejects_duplicate() -> None:
    store = ScheduleStore()
    store.add("t", "g", "@daily")
    with pytest.raises(ValueError):
        store.add("t", "g2", "@hourly")


def test_remove_and_list() -> None:
    store = ScheduleStore()
    store.add("a", "g", "@daily")
    store.add("b", "g", "@hourly")
    assert [t.name for t in store.list_all()] == ["a", "b"]
    assert store.remove("a") is True
    assert store.remove("a") is False
    assert [t.name for t in store.list_all()] == ["b"]


def test_round_trips_to_disk(tmp_path) -> None:
    path = tmp_path / "schedule.toml"
    store = ScheduleStore()
    store.add("nightly", "do the thing", "0 9 * * 1-5", description="weekdays")
    store.save(path)
    assert path.exists()

    reloaded = ScheduleStore.load(path)
    t = reloaded.get("nightly")
    assert t.goal == "do the thing"
    assert t.cron == "0 9 * * 1-5"
    assert t.description == "weekdays"
    assert t.enabled is True


def test_load_missing_is_empty(tmp_path) -> None:
    assert ScheduleStore.load(tmp_path / "nope.toml").list_all() == []


def test_is_due_basic() -> None:
    t = ScheduledTask(name="x", goal="g", cron="0 9 * * *")
    assert t.is_due(datetime(2026, 6, 17, 9, 0))
    assert not t.is_due(datetime(2026, 6, 17, 10, 0))


def test_is_due_respects_enabled() -> None:
    t = ScheduledTask(name="x", goal="g", cron="* * * * *", enabled=False)
    assert not t.is_due(datetime(2026, 6, 17, 9, 0))


def test_is_due_not_twice_in_same_minute() -> None:
    now = datetime(2026, 6, 17, 9, 0)
    t = ScheduledTask(name="x", goal="g", cron="* * * * *", last_run=now.strftime("%Y-%m-%dT%H:%M:%S"))
    assert not t.is_due(now)  # already ran this minute


def test_is_due_catch_up_with_grace() -> None:
    # A @daily task whose last run was yesterday should fire once now within a
    # grace window even if "now" isn't exactly midnight.
    t = ScheduledTask(
        name="x", goal="g", cron="0 9 * * *",
        last_run="2026-06-16T09:00:00",
    )
    # now is 09:05 the next day; with a 10-minute grace the 09:00 firing is caught.
    assert t.is_due(datetime(2026, 6, 17, 9, 5), grace_minutes=10)
    # with no grace, only the exact minute counts → not due at 09:05.
    assert not t.is_due(datetime(2026, 6, 17, 9, 5), grace_minutes=0)


def test_store_due_filters() -> None:
    store = ScheduleStore()
    store.add("on", "g", "0 9 * * *")
    store.add("off", "g", "0 9 * * *")
    store.set_enabled("off", False)
    due = store.due(datetime(2026, 6, 17, 9, 0))
    assert [t.name for t in due] == ["on"]


def test_run_due_executes_records_and_notifies() -> None:
    store = ScheduleStore()
    store.add("a", "goal a", "* * * * *")
    store.add("b", "goal b", "* * * * *")

    ran: list[str] = []
    notes: list[str] = []

    def runner(task) -> RunRecord:
        ran.append(task.name)
        return RunRecord(name=task.name, status="ok", summary=f"did {task.name}")

    now = datetime(2026, 6, 17, 9, 0)
    records = run_due(store, now=now, runner=runner, notifier=notes.append)

    assert sorted(ran) == ["a", "b"]
    assert len(records) == 2
    # store mutated: last_run / status / count recorded
    assert store.get("a").run_count == 1
    assert store.get("a").last_status == "ok"
    assert store.get("a").last_run == now.strftime("%Y-%m-%dT%H:%M:%S")
    # notifications fired, one per task
    assert len(notes) == 2
    assert any("did a" in n for n in notes)


def test_run_due_records_error_and_notifies_failure() -> None:
    store = ScheduleStore()
    store.add("boom", "goal", "* * * * *")
    notes: list[str] = []

    def runner(task) -> RunRecord:
        return RunRecord(name=task.name, status="error", error="kaboom")

    records = run_due(store, now=datetime(2026, 6, 17, 9, 0), runner=runner, notifier=notes.append)
    assert records[0].status == "error"
    assert store.get("boom").last_status == "error"
    assert any("FAILED" in n and "kaboom" in n for n in notes)


def test_run_due_nothing_due_is_noop() -> None:
    store = ScheduleStore()
    store.add("x", "g", "0 9 * * *")
    called: list[str] = []
    records = run_due(
        store, now=datetime(2026, 6, 17, 10, 0),
        runner=lambda t: called.append(t.name) or RunRecord(name=t.name, status="ok"),
    )
    assert records == []
    assert called == []


def test_crontab_line() -> None:
    line = crontab_line()
    assert line.startswith("* * * * *")
    assert "schedule run-due" in line
    assert crontab_line(every_minute=False).startswith("*/5 * * * *")
