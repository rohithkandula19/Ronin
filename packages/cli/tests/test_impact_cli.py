"""CLI wiring for `ronin impact` / `ronin why` / `ronin changed` (Phase 6 change
intelligence). Engine reachability is covered by test_repo_graph.py."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ronin_cli.main import app

runner = CliRunner()


def _mk(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _chain(root: Path) -> Path:
    # a -> b -> c  (c is the leaf everything depends on; a is depended on by nobody)
    _mk(root, "pkg/__init__.py")
    _mk(root, "pkg/a.py", "from pkg import b\n")
    _mk(root, "pkg/b.py", "from pkg import c\n")
    _mk(root, "pkg/c.py", "VALUE = 1\n")
    return root


def test_impact_text(tmp_path: Path):
    _chain(tmp_path)
    r = runner.invoke(app, ["impact", "pkg.c", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "blast radius: 2" in r.output   # a and b reach c
    assert "pkg.b" in r.output


def test_impact_json(tmp_path: Path):
    _chain(tmp_path)
    r = runner.invoke(app, ["impact", "pkg.c", "--root", str(tmp_path), "--format", "json"])
    assert r.exit_code == 0, r.output
    assert '"blast_radius"' in r.output


def test_impact_leaf_is_safe(tmp_path: Path):
    _chain(tmp_path)
    r = runner.invoke(app, ["impact", "pkg.a", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "safe to change in isolation" in r.output


def test_impact_unresolvable_exits_2(tmp_path: Path):
    _chain(tmp_path)
    r = runner.invoke(app, ["impact", "does.not.exist", "--root", str(tmp_path)])
    assert r.exit_code == 2


def test_why(tmp_path: Path):
    _chain(tmp_path)
    r = runner.invoke(app, ["why", "pkg.b", "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "imports 1" in r.output and "imported by 1" in r.output


def test_changed_outside_git_exits_2(tmp_path: Path):
    _chain(tmp_path)   # a bare temp dir is not a git repository
    r = runner.invoke(app, ["changed", "--root", str(tmp_path)])
    assert r.exit_code == 2
