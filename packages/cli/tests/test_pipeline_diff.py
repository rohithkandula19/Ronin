"""Tests for Wave 8 unified-diff capture (read-only, honest)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ronin_cli.pipeline_diff import DiffEvidence, capture_diff


def _init_repo(tmp_path: Path) -> None:
    for args in (("init", "-q"), ("config", "user.email", "t@t.t"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True, capture_output=True)


def test_capture_changed_files(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    ev = capture_diff(tmp_path)
    assert ev.captured is True and ev.full_diff_available is True
    assert "a.py" in ev.files_changed
    assert ev.additions >= 2
    assert "+y = 2" in ev.diff_excerpt and "a.py" in ev.diff_excerpt
    assert ev.truncated is False


def test_capture_no_changes(tmp_path) -> None:
    _init_repo(tmp_path)
    ev = capture_diff(tmp_path)
    assert ev.captured is True and ev.full_diff_available is False
    assert "no tracked changes" in ev.note


def test_capture_outside_git(tmp_path) -> None:
    ev = capture_diff(tmp_path)  # plain dir, no git
    assert ev.captured is False
    assert "not a git repository" in ev.note


def test_capture_disabled(tmp_path) -> None:
    _init_repo(tmp_path)
    ev = capture_diff(tmp_path, include=False)
    assert ev.captured is False and "disabled" in ev.note


def test_capture_truncates_large_diff(tmp_path) -> None:
    _init_repo(tmp_path)
    big = "\n".join(f"line_{i} = {i}" for i in range(2000)) + "\n"
    (tmp_path / "a.py").write_text(big, encoding="utf-8")
    ev = capture_diff(tmp_path, max_bytes=500)
    assert ev.truncated is True and ev.full_diff_available is True
    assert len(ev.diff_excerpt) <= 600  # budget + the truncation notice
    assert "truncated" in ev.diff_excerpt
    assert ev.additions >= 2000  # numstat counts the whole change, not the excerpt


def test_diff_evidence_round_trips(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 9\n", encoding="utf-8")
    ev = capture_diff(tmp_path)
    again = DiffEvidence.model_validate_json(ev.model_dump_json())
    assert again.files_changed == ev.files_changed and again.additions == ev.additions
