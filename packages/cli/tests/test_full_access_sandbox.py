"""Tests for the filesystem sandbox toggle behind --full-access / --god-mode.

sandbox=True (default) confines every path-taking tool to the project root;
sandbox=False (full-access) lifts that so absolute / out-of-root paths resolve.
This is the security-sensitive seam, so both directions are pinned here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.code_tools import build_code_tools


def _tools(root: Path, *, sandbox: bool = True) -> dict:
    return {t.name: t.handler for t in build_code_tools(root, sandbox=sandbox)}


def test_sandboxed_read_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("TOP_SECRET\n", encoding="utf-8")
    read = _tools(root, sandbox=True)["read_file"]
    # absolute path outside root → blocked (tool raises ValueError on escape)
    with pytest.raises(ValueError):
        read(str(secret))


def test_full_access_read_reaches_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("TOP_SECRET\n", encoding="utf-8")
    read = _tools(root, sandbox=False)["read_file"]
    assert "TOP_SECRET" in read(str(secret))


def test_sandboxed_write_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    write = _tools(root, sandbox=True)["write_file"]
    with pytest.raises(ValueError):
        write(str(tmp_path / "escape.txt"), "nope")
    assert not (tmp_path / "escape.txt").exists()


def test_full_access_write_reaches_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    target = tmp_path / "escape.txt"
    write = _tools(root, sandbox=False)["write_file"]
    write(str(target), "written by full-access")
    assert target.read_text(encoding="utf-8") == "written by full-access"


def test_sandbox_still_allows_in_root_paths(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "inside.txt").write_text("OK\n", encoding="utf-8")
    read = _tools(root, sandbox=True)["read_file"]
    assert "OK" in read("inside.txt")
