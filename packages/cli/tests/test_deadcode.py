"""Tests for deadcode — definition collection + dead detection."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.deadcode import definitions_in_source, find_dead


def test_definitions_skip_private_test_main_and_exported() -> None:
    src = (
        "__all__ = ['Keep']\n"
        "def public_fn():\n    pass\n"
        "def _private():\n    pass\n"
        "def test_thing():\n    pass\n"
        "def main():\n    pass\n"
        "class Keep:\n    pass\n"
    )
    names = {d.name for d in definitions_in_source(src, "m.py")}
    assert names == {"public_fn"}          # private/test/main/__all__ all excluded


def test_definitions_flags_decorated() -> None:
    src = "import x\n@x.command()\ndef cmd():\n    pass\ndef plain():\n    pass\n"
    by = {d.name: d for d in definitions_in_source(src, "m.py")}
    assert by["cmd"].decorated is True
    assert by["plain"].decorated is False


def test_find_dead_flags_unreferenced(tmp_path: Path) -> None:
    (tmp_path / "lib.py").write_text(
        "def used_helper():\n    return 1\n"
        "def orphan():\n    return 2\n",
        encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "from lib import used_helper\n"
        "def run():\n    return used_helper()\n",   # used_helper referenced; orphan not
        encoding="utf-8")
    dead = {d.name for d in find_dead(tmp_path)}
    assert "orphan" in dead
    assert "used_helper" not in dead


def test_find_dead_excludes_decorated_by_default(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "import typer\napp = typer.Typer()\n"
        "@app.command()\ndef cli_cmd():\n    pass\n",   # decorated → not flagged
        encoding="utf-8")
    dead = {d.name for d in find_dead(tmp_path)}
    assert "cli_cmd" not in dead
    # but with include_decorated it shows up
    dead2 = {d.name for d in find_dead(tmp_path, include_decorated=True)}
    assert "cli_cmd" in dead2


def test_find_dead_respects_usage_in_same_file(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def helper():\n    return 1\n"
        "def caller():\n    return helper()\n",
        encoding="utf-8")
    # helper is used by caller; caller itself is unreferenced → caller is dead
    dead = {d.name for d in find_dead(tmp_path)}
    assert "helper" not in dead
    assert "caller" in dead


def test_find_dead_empty_when_all_used(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def a():\n    return 1\n"
        "def b():\n    return a()\n"
        "print(b())\n",
        encoding="utf-8")
    assert find_dead(tmp_path) == []
