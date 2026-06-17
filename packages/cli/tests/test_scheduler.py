"""Offline tests for the task scheduler: store, list, next-run, due, run-due.

Everything here runs with no API key and no network - the cron matcher is pure,
persistence is a JSON file under a tmp HOME, and ``run_due`` is exercised with an
injected stub runner so no model is ever called.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli import scheduler
from ronin_cli.config import RoninConfig
from ronin_cli.main import app

runner = CliRunner()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ---------- pure cron parsing / matching ----------

def test_parse_cron_valid_and_invalid() -> None:
    assert scheduler.is_valid_cron("0 9 * * 1-5")
    assert scheduler.is_valid_cron("@daily")
    assert scheduler.is_valid_cron("*/15 9-17 * * *")
    assert not scheduler.is_valid_cron("nonsense")
    assert not scheduler.is_valid_cron("0 9 * *")        # 4 fields
    assert not scheduler.is_valid_cron("99 * * * *")     # minute out of range


def test_next_run_after_daily() -> None:
    base = datetime(2026, 6, 17, 8, 30)  # before 09:00
    nxt = scheduler.next_run_after("0 9 * * *", base)
    assert nxt == datetime(2026, 6, 17, 9, 0)


def test_next_run_after_weekday_only() -> None:
    # 0 9 * * 1-5 = weekdays at 09:00. 2026-06-20 is a Saturday; next weekday is Mon 22nd.
    base = datetime(2026, 6, 20, 10, 0)  # Saturday after 9am
    nxt = scheduler.next_run_after("0 9 * * 1-5", base)
    assert nxt == datetime(2026, 6, 22, 9, 0)  # Monday


def test_next_run_impossible_date_returns_none() -> None:
    # 31st of February never happens.
    assert scheduler.next_run_after("0 0 31 2 *", datetime(2026, 1, 1, 0, 0)) is None


# ---------- store / list / remove ----------

def test_add_list_remove(home: Path) -> None:
    assert scheduler.load_tasks() == []
    t = scheduler.add_task("morning briefing", "summarize my MRR", "0 9 * * 1-5")
    assert t.name == "morning-briefing"
    assert (home / ".ronin" / "schedule.json").is_file()
    names = [x.name for x in scheduler.load_tasks()]
    assert names == ["morning-briefing"]
    assert scheduler.remove_task("morning-briefing") is True
    assert scheduler.load_tasks() == []
    assert scheduler.remove_task("morning-briefing") is False


def test_add_rejects_bad_input(home: Path) -> None:
    with pytest.raises(ValueError):
        scheduler.add_task("ok-name", "  ", "0 9 * * *")  # empty prompt
    with pytest.raises(ValueError):
        scheduler.add_task("ok-name", "do thing", "not-a-cron")  # bad cron
    assert scheduler.load_tasks() == []


def test_add_replaces_same_name(home: Path) -> None:
    scheduler.add_task("daily", "first", "@daily")
    scheduler.add_task("daily", "second", "@daily")
    tasks = scheduler.load_tasks()
    assert len(tasks) == 1 and tasks[0].prompt == "second"


# ---------- due logic ----------

def test_is_due_and_due_tasks(home: Path) -> None:
    scheduler.add_task("nine", "do A", "0 9 * * *")
    scheduler.add_task("ten", "do B", "0 10 * * *")
    at_nine = datetime(2026, 6, 17, 9, 0)
    due = scheduler.due_tasks(scheduler.load_tasks(), at_nine)
    assert [t.name for t in due] == ["nine"]


def test_is_due_skips_already_run_this_minute(home: Path) -> None:
    scheduler.add_task("nine", "do A", "0 9 * * *")
    at_nine = datetime(2026, 6, 17, 9, 0)
    assert scheduler.due_tasks(scheduler.load_tasks(), at_nine)
    scheduler.mark_ran("nine", when=at_nine.timestamp())
    # Same minute -> no longer due (idempotent within the minute).
    assert scheduler.due_tasks(scheduler.load_tasks(), at_nine) == []


def test_disabled_task_never_due(home: Path) -> None:
    scheduler.add_task("nine", "do A", "0 9 * * *")
    tasks = scheduler.load_tasks()
    tasks[0].enabled = False
    scheduler._save(tasks)
    assert scheduler.due_tasks(scheduler.load_tasks(), datetime(2026, 6, 17, 9, 0)) == []


# ---------- run_due actually runs the agent (stub runner; no model) ----------

def test_run_due_runs_each_due_task_and_marks_it(home: Path) -> None:
    scheduler.add_task("nine", "what is my MRR", "0 9 * * *")
    scheduler.add_task("ten", "later", "0 10 * * *")
    at_nine = datetime(2026, 6, 17, 9, 0)

    calls: list[str] = []

    class _Res:
        def __init__(self, prompt: str) -> None:
            self.success = True
            self.output = f"answer to: {prompt}"
            self.error = None

    def fake_runner(cfg, prompt):
        calls.append(prompt)
        return _Res(prompt)

    results = scheduler.run_due(RoninConfig(demo_mode=True), at_nine, runner=fake_runner)
    assert calls == ["what is my MRR"]            # only the due one ran
    assert len(results) == 1 and results[0].success
    assert "MRR" in results[0].output
    # It was recorded as having run, so it won't double-fire this minute.
    ran = scheduler.get_task("nine")
    assert ran is not None and ran.runs == 1 and ran.last_run is not None
    assert scheduler.due_tasks(scheduler.load_tasks(), at_nine) == []


def test_run_due_isolates_a_failing_task(home: Path) -> None:
    scheduler.add_task("boom", "explode", "0 9 * * *")
    at_nine = datetime(2026, 6, 17, 9, 0)

    def boom_runner(cfg, prompt):
        raise RuntimeError("provider exploded")

    results = scheduler.run_due(RoninConfig(demo_mode=True), at_nine, runner=boom_runner)
    assert len(results) == 1 and results[0].success is False
    assert "provider exploded" in (results[0].error or "")
    # Still marked ran (so it won't hammer every tick within the minute).
    assert scheduler.get_task("boom").runs == 1


def test_run_due_default_runner_uses_offline_demo_brain(home: Path) -> None:
    # No API key, demo_mode -> run_ask uses the offline demo brain (no network).
    scheduler.add_task("nine", "what is my current MRR", "0 9 * * *")
    at_nine = datetime(2026, 6, 17, 9, 0)
    results = scheduler.run_due(RoninConfig(demo_mode=True), at_nine)  # default runner
    assert len(results) == 1
    assert results[0].output.strip()  # the demo brain produced an answer offline


# ---------- CLI surface ----------

def test_cli_add_list_run_remove(home: Path) -> None:
    r = runner.invoke(app, ["schedule", "add", "briefing", "summarize MRR", "--cron", "0 9 * * 1-5"])
    assert r.exit_code == 0 and "stored" in r.stdout

    r = runner.invoke(app, ["schedule", "list"])
    assert r.exit_code == 0 and "briefing" in r.stdout and "0 9 * * 1-5" in r.stdout

    # run-due at a time it is due (demo mode via default config: still offline demo brain).
    r = runner.invoke(app, ["schedule", "run-due", "--at", "2026-06-22 09:00"])  # a Monday
    assert r.exit_code == 0 and "briefing" in r.stdout

    r = runner.invoke(app, ["schedule", "remove", "briefing"])
    assert r.exit_code == 0 and "removed" in r.stdout

    r = runner.invoke(app, ["schedule", "list"])
    assert "no scheduled tasks yet" in r.stdout


def test_cli_add_rejects_bad_cron(home: Path) -> None:
    r = runner.invoke(app, ["schedule", "add", "x", "do thing", "--cron", "not-a-cron"])
    assert r.exit_code == 1 and "invalid cron" in r.stdout
