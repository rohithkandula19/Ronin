"""CLI-level tests for `ronin schedule ...` and `ronin doctor` (offline).

These exercise the actual typer wiring (add/list/remove/run-due/doctor) against a
temp working dir so the .ronin store and the notifications log are isolated.
"""
from __future__ import annotations

import os

from typer.testing import CliRunner

from ronin_cli.main import app

runner = CliRunner()


def _run(args, cwd, home):
    """Invoke the CLI with cwd + HOME pinned to temp dirs (so the project store
    at ./.ronin and the user log at ~/.ronin are both sandboxed)."""
    old_cwd = os.getcwd()
    old_home = os.environ.get("HOME")
    os.chdir(cwd)
    os.environ["HOME"] = str(home)
    try:
        return runner.invoke(app, args)
    finally:
        os.chdir(old_cwd)
        if old_home is not None:
            os.environ["HOME"] = old_home


def test_schedule_add_list_remove(tmp_path) -> None:
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir()
    home.mkdir()

    r = _run(["schedule", "add", "nightly", "@daily", "summarize the day"], proj, home)
    assert r.exit_code == 0, r.output
    assert "scheduled" in r.output and "nightly" in r.output
    assert (proj / ".ronin" / "schedule.toml").exists()

    r = _run(["schedule", "list"], proj, home)
    assert r.exit_code == 0
    assert "nightly" in r.output

    r = _run(["schedule", "remove", "nightly"], proj, home)
    assert r.exit_code == 0 and "removed" in r.output

    r = _run(["schedule", "list"], proj, home)
    assert "no scheduled tasks" in r.output


def test_schedule_add_rejects_bad_cron(tmp_path) -> None:
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir()
    home.mkdir()
    r = _run(["schedule", "add", "x", "totally-bogus", "g"], proj, home)
    assert r.exit_code != 0
    assert "invalid cron" in r.output


def test_schedule_run_due_dry_run(tmp_path) -> None:
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir()
    home.mkdir()
    _run(["schedule", "add", "ping", "* * * * *", "do a thing"], proj, home)
    r = _run(["schedule", "run-due", "--dry-run"], proj, home)
    assert r.exit_code == 0
    assert "due" in r.output and "ping" in r.output
    # dry-run must not have recorded a run
    text = (proj / ".ronin" / "schedule.toml").read_text()
    assert "last_run" not in text


def test_schedule_run_due_real_in_demo_mode(tmp_path) -> None:
    """A full run-due in demo mode: real agent (offline demo brain), real store
    update, real local notification — no network, no key."""
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir()
    home.mkdir()
    (proj / ".ronin").mkdir()
    (proj / ".ronin" / "config.toml").write_text("demo_mode = true\n")

    _run(["schedule", "add", "ping", "* * * * *", "what is the revenue this month"], proj, home)
    r = _run(["schedule", "run-due", "--no-webhook"], proj, home)
    assert r.exit_code == 0, r.output
    assert "ping" in r.output

    # store recorded the run
    text = (proj / ".ronin" / "schedule.toml").read_text()
    assert "last_run" in text and 'last_status = "ok"' in text

    # local notification was written to ~/.ronin/notifications.log
    log = home / ".ronin" / "notifications.log"
    assert log.exists()
    assert "ping" in log.read_text()


def test_schedule_cron_line(tmp_path) -> None:
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir()
    home.mkdir()
    r = _run(["schedule", "cron-line"], proj, home)
    assert r.exit_code == 0
    assert "schedule run-due" in r.output


def test_doctor_runs_and_reports(tmp_path, monkeypatch) -> None:
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir()
    home.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # No config, no key -> auth fails, problems + fixes printed, but report-only
    # mode still exits 0 (so doctor stays a glanceable status report).
    r = _run(["doctor"], proj, home)
    assert "problems found" in r.output
    assert "fix:" in r.output
    assert r.exit_code == 0


def test_doctor_strict_gates_on_problem(tmp_path, monkeypatch) -> None:
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir()
    home.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # --strict turns any problem into a non-zero exit (CI / setup gate).
    r = _run(["doctor", "--strict"], proj, home)
    assert r.exit_code == 1


def test_doctor_demo_mode_passes(tmp_path) -> None:
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir()
    home.mkdir()
    (proj / ".ronin").mkdir()
    (proj / ".ronin" / "config.toml").write_text("demo_mode = true\n")
    r = _run(["doctor"], proj, home)
    assert r.exit_code == 0
    assert "all checks passed" in r.output
