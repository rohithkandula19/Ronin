"""Tests for the background / async agent task layer.

Covers the PURE, deterministic core only — the state machine (``new_record`` /
``advance``) and the table renderer (``summarize_tasks``) — plus the disk
helpers driven against a temp ``RONIN_HOME`` with NO real subprocess and NO
network (``start_task`` is monkeypatched so nothing is actually spawned). Times
are injected, so every assertion is exact.
"""
from __future__ import annotations

import json

from ronin_cli import background as bg


# --------------------------------------------------------------------------
# new_record — shape + defaults
# --------------------------------------------------------------------------

def test_new_record_shape() -> None:
    rec = bg.new_record("bg-abc123", "investigate the flaky test", "ask", now=100.0)
    assert rec["id"] == "bg-abc123"
    assert rec["prompt"] == "investigate the flaky test"
    assert rec["kind"] == "ask"
    assert rec["status"] == bg.STATUS_QUEUED
    assert rec["created_at"] == 100.0
    assert rec["started_at"] is None
    assert rec["ended_at"] is None
    assert rec["elapsed"] == 0.0
    assert rec["result"] is None
    assert rec["error"] is None
    assert rec["pid"] is None


def test_new_record_unknown_kind_falls_back_to_ask() -> None:
    rec = bg.new_record("bg-1", "x", "destroy-everything", now=0.0)
    assert rec["kind"] == "ask"


def test_new_record_code_kind_preserved() -> None:
    rec = bg.new_record("bg-1", "refactor module", "code", now=0.0)
    assert rec["kind"] == "code"


def test_new_record_is_json_round_trippable() -> None:
    rec = bg.new_record("bg-1", "do x", "ask", now=5.0)
    assert json.loads(json.dumps(rec)) == rec


# --------------------------------------------------------------------------
# advance — state transitions, timestamps, elapsed, purity
# --------------------------------------------------------------------------

def test_advance_to_running_stamps_started_at() -> None:
    rec = bg.new_record("bg-1", "x", "ask", now=10.0)
    run = bg.advance(rec, bg.STATUS_RUNNING, now=12.5)
    assert run["status"] == bg.STATUS_RUNNING
    assert run["started_at"] == 12.5
    assert run["ended_at"] is None
    # elapsed-so-far is measured from started_at (== now on the first tick) → 0.
    assert run["elapsed"] == 0.0


def test_advance_does_not_mutate_input() -> None:
    rec = bg.new_record("bg-1", "x", "ask", now=10.0)
    before = json.dumps(rec)
    bg.advance(rec, bg.STATUS_RUNNING, now=20.0)
    assert json.dumps(rec) == before  # original untouched (pure)


def test_advance_running_then_done_computes_elapsed_from_start() -> None:
    rec = bg.new_record("bg-1", "x", "ask", now=0.0)
    rec = bg.advance(rec, bg.STATUS_RUNNING, now=3.0)
    done = bg.advance(rec, bg.STATUS_DONE, now=11.0, result="the answer")
    assert done["status"] == bg.STATUS_DONE
    assert done["started_at"] == 3.0
    assert done["ended_at"] == 11.0
    assert done["elapsed"] == 8.0  # 11 - 3, NOT 11 - created_at
    assert done["result"] == "the answer"
    assert done["error"] is None


def test_advance_failed_records_error() -> None:
    rec = bg.new_record("bg-1", "x", "ask", now=0.0)
    rec = bg.advance(rec, bg.STATUS_RUNNING, now=1.0)
    failed = bg.advance(rec, bg.STATUS_FAILED, now=4.0, error="boom")
    assert failed["status"] == bg.STATUS_FAILED
    assert failed["error"] == "boom"
    assert failed["ended_at"] == 4.0
    assert failed["elapsed"] == 3.0


def test_advance_terminal_without_start_uses_created_at() -> None:
    # A task cancelled while still queued never started → elapsed off created_at.
    rec = bg.new_record("bg-1", "x", "ask", now=100.0)
    cancelled = bg.advance(rec, bg.STATUS_CANCELLED, now=105.0, error="cancelled")
    assert cancelled["status"] == bg.STATUS_CANCELLED
    assert cancelled["started_at"] is None
    assert cancelled["elapsed"] == 5.0


def test_advance_elapsed_never_negative() -> None:
    rec = bg.new_record("bg-1", "x", "ask", now=50.0)
    rec = bg.advance(rec, bg.STATUS_RUNNING, now=60.0)
    # A clock that went backwards must not yield a negative elapsed.
    weird = bg.advance(rec, bg.STATUS_DONE, now=55.0)
    assert weird["elapsed"] == 0.0


# --------------------------------------------------------------------------
# summarize_tasks — rendering
# --------------------------------------------------------------------------

def test_summarize_empty() -> None:
    out = bg.summarize_tasks([])
    assert "no background tasks" in out


def test_summarize_renders_header_and_rows() -> None:
    a = bg.advance(bg.new_record("bg-aaa", "summarize the repo", "ask", now=0.0),
                   bg.STATUS_DONE, now=4.0, result="done")
    b = bg.new_record("bg-bbb", "plan a refactor", "code", now=10.0)  # queued
    out = bg.summarize_tasks([a, b])
    # header
    assert "ID" in out and "KIND" in out and "STATUS" in out and "ELAPSED" in out
    # both ids present
    assert "bg-aaa" in out and "bg-bbb" in out
    # statuses + kinds rendered
    assert "done" in out and "queued" in out
    assert "ask" in out and "code" in out
    # elapsed formatted (4s for the finished one)
    assert "4s" in out


def test_summarize_newest_first() -> None:
    old = bg.new_record("bg-old", "old task", "ask", now=1.0)
    new = bg.new_record("bg-new", "new task", "ask", now=999.0)
    out = bg.summarize_tasks([old, new])
    assert out.index("bg-new") < out.index("bg-old")


def test_summarize_truncates_long_prompt() -> None:
    long = "x" * 200
    rec = bg.new_record("bg-1", long, "ask", now=0.0)
    out = bg.summarize_tasks([rec])
    assert "…" in out
    assert "x" * 200 not in out


def test_fmt_elapsed_units() -> None:
    assert bg._fmt_elapsed(0) == "0s"
    assert bg._fmt_elapsed(45) == "45s"
    assert bg._fmt_elapsed(133) == "2m13s"
    assert bg._fmt_elapsed(3700) == "1h01m"


# --------------------------------------------------------------------------
# Disk helpers — driven against a temp RONIN_HOME (no subprocess, no network)
# --------------------------------------------------------------------------

def test_list_and_get_tolerate_missing_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    assert bg.list_tasks() == []
    assert bg.get_task("bg-nope") is None


def test_write_get_list_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    rec = bg.new_record("bg-zzz", "do the thing", "ask", now=7.0)
    bg._write_record(rec)
    assert bg.get_task("bg-zzz") == rec
    listed = bg.list_tasks()
    assert len(listed) == 1 and listed[0]["id"] == "bg-zzz"
    # file really landed under <home>/bg/
    assert (tmp_path / "bg" / "bg-zzz.json").is_file()


def test_list_skips_corrupt_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    good = bg.new_record("bg-good", "ok", "ask", now=1.0)
    bg._write_record(good)
    (tmp_path / "bg" / "bg-bad.json").write_text("{ not json", encoding="utf-8")
    listed = bg.list_tasks()
    assert [r["id"] for r in listed] == ["bg-good"]  # corrupt one skipped


def test_cancel_unknown_returns_false(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    assert bg.cancel_task("bg-ghost") is False


def test_cancel_marks_record_cancelled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    rec = bg.new_record("bg-c", "x", "ask", now=0.0)
    rec["pid"] = None  # no live worker → just a state flip
    bg._write_record(rec)
    assert bg.cancel_task("bg-c") is True
    after = bg.get_task("bg-c")
    assert after["status"] == bg.STATUS_CANCELLED


def test_cancel_terminal_is_noop_but_true(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    rec = bg.advance(bg.new_record("bg-d", "x", "ask", now=0.0),
                     bg.STATUS_DONE, now=2.0, result="r")
    bg._write_record(rec)
    assert bg.cancel_task("bg-d") is True
    assert bg.get_task("bg-d")["status"] == bg.STATUS_DONE  # unchanged


# --------------------------------------------------------------------------
# start_task — id returned immediately; subprocess.Popen is mocked away
# --------------------------------------------------------------------------

def test_start_task_returns_id_without_spawning(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))

    class _FakeProc:
        pid = 4242

    spawned: dict = {}

    def _fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["detached"] = kwargs.get("start_new_session")
        return _FakeProc()

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    tid = bg.start_task("do a big thing", root=str(tmp_path), kind="ask")
    assert tid.startswith("bg-")
    # a queued/running record was persisted with the pid stamped
    rec = bg.get_task(tid)
    assert rec is not None
    assert rec["prompt"] == "do a big thing"
    assert rec["pid"] == 4242
    # launched detached, via `python -m ronin_cli.background --worker <id>`
    assert spawned["detached"] is True
    assert "ronin_cli.background" in spawned["cmd"]
    assert "--worker" in spawned["cmd"] and tid in spawned["cmd"]


def test_start_task_records_launch_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))

    def _boom(cmd, **kwargs):
        raise OSError("no fork for you")

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", _boom)

    tid = bg.start_task("x", root=str(tmp_path), kind="ask")
    rec = bg.get_task(tid)
    assert rec["status"] == bg.STATUS_FAILED
    assert "failed to launch worker" in (rec["error"] or "")


# --------------------------------------------------------------------------
# notify_done — never raises, always prints a line (no audio/webhook in test)
# --------------------------------------------------------------------------

def test_notify_done_prints_and_never_raises(capsys, monkeypatch) -> None:
    # Make sure no webhook is attempted and audio is a no-op regardless of host.
    monkeypatch.delenv("RONIN_NOTIFY_WEBHOOK", raising=False)
    rec = bg.advance(bg.new_record("bg-n", "x", "ask", now=0.0),
                     bg.STATUS_DONE, now=3.0, result="r")
    bg.notify_done(rec)  # console=None → prints to stdout
    out = capsys.readouterr().out
    assert "bg-n" in out and "done" in out
