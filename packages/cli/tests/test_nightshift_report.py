"""Tests for Nightshift report persistence + patch application."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.nightshift import (
    Task,
    TaskResult,
    applicable,
    apply_patch,
    gather_tasks,
    load_done,
    load_report,
    mark_done,
    save_report,
    task_signature,
)


def test_task_signature_stable_and_distinct() -> None:
    a = Task("todo", "fix x", "d", ref="a.py:1")
    a2 = Task("todo", "fix x", "different detail", ref="a.py:1")  # detail not in sig
    b = Task("todo", "fix x", "d", ref="a.py:2")
    assert task_signature(a) == task_signature(a2)
    assert task_signature(a) != task_signature(b)


def test_done_ledger_skips_completed(tmp_path) -> None:
    (tmp_path / "m.py").write_text("# TODO: alpha\n# TODO: beta\n", encoding="utf-8")
    first = gather_tasks(tmp_path)
    assert len(first) == 2
    mark_done(tmp_path, first[0])              # ship the first
    again = gather_tasks(tmp_path)             # skip_done default
    assert len(again) == 1 and again[0].title == first[1].title
    # --redo equivalent: skip_done=False brings it back
    assert len(gather_tasks(tmp_path, skip_done=False)) == 2
    assert task_signature(first[0]) in load_done(tmp_path)


def _results() -> list[TaskResult]:
    return [
        TaskResult(Task("todo", "fix a", "d", ref="a.py:1"), "patched",
                   note="3 passed", patch_path="/tmp/01.patch"),
        TaskResult(Task("issue", "feat b", "d", ref="#5"), "patched",
                   note="ok", patch_path="/tmp/02.patch", blockers=["race condition"]),
        TaskResult(Task("todo", "c", "d"), "failed-tests", note="1 failed"),
    ]


def test_report_round_trip(tmp_path: Path) -> None:
    save_report(tmp_path, _results())
    assert (tmp_path / ".ronin" / "nightshift" / "report.json").is_file()
    rep = load_report(tmp_path)
    assert len(rep) == 3
    assert rep[0]["title"] == "fix a" and rep[0]["status"] == "patched"
    assert rep[1]["blockers"] == ["race condition"]


def test_load_missing_report(tmp_path: Path) -> None:
    assert load_report(tmp_path) == []


def test_applicable_filters() -> None:
    rep = [
        {"status": "patched", "patch_path": "/a", "blockers": []},
        {"status": "patched", "patch_path": "/b", "blockers": ["bug"]},
        {"status": "failed-tests", "patch_path": "", "blockers": []},
        {"status": "patched", "patch_path": "", "blockers": []},   # no patch file path
    ]
    assert len(applicable(rep)) == 2                       # both patched-with-path
    assert len(applicable(rep, clean_only=True)) == 1      # the flagged one dropped


def test_apply_patch_real(tmp_path: Path) -> None:
    import subprocess
    # build a tiny repo + a committed file, then a patch that edits it
    subprocess.run(["git", "-C", str(tmp_path), "init"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.t"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], capture_output=True)
    f = tmp_path / "x.txt"
    f.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True)
    diff = subprocess.run(["git", "-C", str(tmp_path), "diff"], capture_output=True, text=True)
    f.write_text("hello world\n", encoding="utf-8")
    diff = subprocess.run(["git", "-C", str(tmp_path), "diff"], capture_output=True, text=True).stdout
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "x.txt"], capture_output=True)  # revert
    patch = tmp_path / "p.patch"
    patch.write_text(diff, encoding="utf-8")
    assert apply_patch(tmp_path, str(patch)) is True
    assert f.read_text(encoding="utf-8") == "hello world\n"
    assert apply_patch(tmp_path, "/nonexistent.patch") is False
