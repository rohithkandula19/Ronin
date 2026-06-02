"""Tests for The Ghost — snapshot/diff core, reactions, and the tick state."""
from __future__ import annotations

import os
from pathlib import Path

from ronin_cli.ghost import (
    GhostState,
    PANDA,
    changed_files,
    react,
    snapshot,
)


def test_snapshot_watches_source_skips_vendor(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hi\n", encoding="utf-8")  # not a watched suffix
    vend = tmp_path / "node_modules"
    vend.mkdir()
    (vend / "b.js").write_text("//\n", encoding="utf-8")
    snap = snapshot(tmp_path)
    assert "a.py" in snap
    assert "notes.txt" not in snap
    assert not any("node_modules" in f for f in snap)


def test_changed_files_detects_add_and_modify(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x=1\n", encoding="utf-8")
    old = snapshot(tmp_path)
    # add a file + bump mtime of the existing one
    (tmp_path / "b.py").write_text("y=2\n", encoding="utf-8")
    future = os.path.getmtime(f) + 10
    os.utime(f, (future, future))
    new = snapshot(tmp_path)
    changed = changed_files(old, new)
    assert "a.py" in changed and "b.py" in changed


def test_changed_files_none_when_static(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    snap = snapshot(tmp_path)
    assert changed_files(snap, snap) == []


def test_react_faces() -> None:
    assert PANDA["pass"] in react("pass", "534 passed")
    assert "534 passed" in react("pass", "534 passed")
    assert PANDA["fail"] in react("fail")
    assert react("idle")  # non-empty


def test_ghost_state_tick() -> None:
    st = GhostState()
    # first tick with a non-empty snapshot establishes a baseline, no "changes"
    assert st.tick({"a.py": 1.0}) == []
    assert st.saves == 0
    # a later tick with a newer mtime reports the change and counts the save
    changed = st.tick({"a.py": 2.0})
    assert changed == ["a.py"] and st.saves == 1
    assert st.last_change == ["a.py"]
