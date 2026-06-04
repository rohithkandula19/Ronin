"""Tests for watch — snapshot + change detection."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.watch import changed, describe_change, snapshot


def test_snapshot_only_watched_suffixes(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hi\n", encoding="utf-8")
    snap = snapshot(tmp_path)
    names = {Path(p).name for p in snap}
    assert "a.py" in names and "b.txt" not in names


def test_snapshot_skips_noise_dirs(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")
    noise = tmp_path / ".venv"
    noise.mkdir()
    (noise / "dep.py").write_text("y = 2\n", encoding="utf-8")
    snap = snapshot(tmp_path)
    names = {Path(p).name for p in snap}
    assert "real.py" in names and "dep.py" not in names


def test_changed_detects_add_modify_delete() -> None:
    old = {"a.py": 1.0, "b.py": 2.0}
    # b modified, c added, a deleted
    new = {"b.py": 3.0, "c.py": 5.0}
    hits = changed(old, new)
    assert hits == {"a.py", "b.py", "c.py"}


def test_changed_none_when_identical() -> None:
    snap = {"a.py": 1.0, "b.py": 2.0}
    assert changed(snap, dict(snap)) == set()


def test_describe_change(tmp_path: Path) -> None:
    paths = {str(tmp_path / f"f{i}.py") for i in range(5)}
    desc = describe_change(paths, tmp_path)
    assert "more" in desc                          # >3 → "(+2 more)"
    assert describe_change(set(), tmp_path) == "no files"
    one = describe_change({str(tmp_path / "solo.py")}, tmp_path)
    assert one == "solo.py"
