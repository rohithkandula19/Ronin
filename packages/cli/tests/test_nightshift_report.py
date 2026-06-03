"""Tests for Nightshift report persistence + patch application."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.nightshift import (
    Task,
    TaskResult,
    applicable,
    apply_patch,
    load_report,
    save_report,
)


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
