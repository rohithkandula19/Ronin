"""CLI wiring for `ronin graph` / `ronin architecture` (Phase 6). Engine
correctness is covered by test_repo_graph.py; this locks the command surface."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ronin_cli.main import app

runner = CliRunner()


def _mk(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _cyclic(root: Path) -> Path:
    _mk(root, "pkg/__init__.py")
    _mk(root, "pkg/a.py", "from pkg import b\n")
    _mk(root, "pkg/b.py", "from pkg import a\n")   # a<->b cycle
    _mk(root, "pyproject.toml", "[project]\nname='x'\nversion='0'\n")
    return root


def test_graph_text(tmp_path: Path):
    _cyclic(tmp_path)
    r = runner.invoke(app, ["graph", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "modules:" in r.output and "cycles: 1" in r.output


def test_graph_json_has_keys(tmp_path: Path):
    _cyclic(tmp_path)
    r = runner.invoke(app, ["graph", str(tmp_path), "--format", "json"])
    assert r.exit_code == 0, r.output
    assert '"modules"' in r.output and '"edges"' in r.output


def test_graph_cycles_flag(tmp_path: Path):
    _cyclic(tmp_path)
    r = runner.invoke(app, ["graph", str(tmp_path), "--cycles"])
    assert r.exit_code == 0, r.output
    assert "circular import group" in r.output


def test_graph_strict_exits_nonzero_on_cycle(tmp_path: Path):
    _cyclic(tmp_path)
    r = runner.invoke(app, ["graph", str(tmp_path), "--strict"])
    assert r.exit_code == 1, r.output


def test_graph_empty_dir_exits_2(tmp_path: Path):
    r = runner.invoke(app, ["graph", str(tmp_path)])
    assert r.exit_code == 2


def test_architecture_text(tmp_path: Path):
    _cyclic(tmp_path)
    r = runner.invoke(app, ["architecture", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "stack" in r.output and "packages" in r.output


def test_architecture_json(tmp_path: Path):
    _cyclic(tmp_path)
    r = runner.invoke(app, ["architecture", str(tmp_path), "--format", "json"])
    assert r.exit_code == 0, r.output
    assert '"stack"' in r.output and '"packages"' in r.output
