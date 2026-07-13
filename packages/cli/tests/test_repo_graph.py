"""Ronin v1.1 / Phase 6 — the module-level import graph engine (repo_graph)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ronin_cli import repo_graph as rg


def _mk(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    # package `pkg` with a 3-module cycle a->b->c->a, an orphan d, and a leaf e
    # that a depends on; plus external + stdlib imports that must NOT be nodes.
    _mk(tmp_path, "pkg/__init__.py")
    _mk(tmp_path, "pkg/a.py", "import os\nimport anthropic\nfrom pkg import b\nfrom pkg.e import helper\n")
    _mk(tmp_path, "pkg/b.py", "from pkg import c\n")
    _mk(tmp_path, "pkg/c.py", "from . import a\n")   # relative import closes the cycle
    _mk(tmp_path, "pkg/d.py", "VALUE = 1\n")           # orphan (no in/out edges)
    _mk(tmp_path, "pkg/e.py", "def helper():\n    return 1\n")
    _mk(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    _mk(tmp_path, ".venv/lib/junk.py", "from pkg import a\n")  # ignored dir
    return tmp_path


def test_build_edges_and_module_names(sample: Path):
    g = rg.build_import_graph(sample)
    mods = set(g.modules())
    assert {"pkg", "pkg.a", "pkg.b", "pkg.c", "pkg.d", "pkg.e"} <= mods
    # ignored directory contributes no module
    assert not any("junk" in m for m in mods)
    # submodule edges, not package edges
    assert "pkg.b" in g.edges["pkg.a"]
    assert "pkg.e" in g.edges["pkg.a"]
    assert "pkg.c" in g.edges["pkg.b"]
    assert "pkg.a" in g.edges["pkg.c"]     # relative `from . import a`
    # external / stdlib imports are recorded but never become graph nodes
    assert "anthropic" in g.external["pkg.a"]
    assert "os" in g.external["pkg.a"]
    assert "os" not in mods and "anthropic" not in mods


def test_cycle_detection(sample: Path):
    g = rg.build_import_graph(sample)
    cycles = rg.find_cycles(g)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"pkg.a", "pkg.b", "pkg.c"}


def test_no_false_cycle_on_a_dag(tmp_path: Path):
    _mk(tmp_path, "app/__init__.py")
    _mk(tmp_path, "app/x.py", "from app import y\n")
    _mk(tmp_path, "app/y.py", "from app import z\n")
    _mk(tmp_path, "app/z.py", "VALUE = 1\n")
    g = rg.build_import_graph(tmp_path)
    assert rg.find_cycles(g) == []


def test_metrics(sample: Path):
    g = rg.build_import_graph(sample)
    m = rg.metrics(g)
    assert m["modules"] >= 6
    assert m["cycles"] == 1
    assert "pkg.d" in m["orphans"]
    # pkg.a is depended on by pkg.c
    assert m["fan_in"].get("pkg.a", 0) >= 1


def test_json_render_is_valid_and_complete(sample: Path):
    g = rg.build_import_graph(sample)
    payload = json.loads(rg.to_json(g))
    assert "pkg.b" in payload["edges"]["pkg.a"]
    assert payload["metrics"]["cycles"] == 1
    assert any(set(c) == {"pkg.a", "pkg.b", "pkg.c"} for c in payload["cycles"])


def test_mermaid_render(sample: Path):
    g = rg.build_import_graph(sample)
    out = rg.to_mermaid(g)
    assert out.startswith("graph LR")
    assert "-->" in out
    assert "pkg.a" in out  # label present


def test_mermaid_truncates_large_graphs(tmp_path: Path):
    _mk(tmp_path, "big/__init__.py")
    for i in range(120):
        _mk(tmp_path, f"big/m{i}.py", "from big import m0\n" if i else "VALUE = 1\n")
    g = rg.build_import_graph(tmp_path)
    out = rg.to_mermaid(g, max_nodes=30)
    node_decls = [ln for ln in out.splitlines() if '["' in ln]  # declarations, not edges
    assert len(node_decls) <= 30
    assert "omitted for readability" in out


def test_detect_stack(sample: Path):
    g = rg.build_import_graph(sample)
    stack = rg.detect_stack(sample, g)
    assert "Python (pyproject)" in stack["manifests"]
    assert "Anthropic SDK" in stack["frameworks"]


def test_unparseable_file_is_skipped(tmp_path: Path):
    _mk(tmp_path, "pkg/__init__.py")
    _mk(tmp_path, "pkg/good.py", "VALUE = 1\n")
    _mk(tmp_path, "pkg/bad.py", "def broken( : this is not python\n")
    g = rg.build_import_graph(tmp_path)  # must not raise
    assert "pkg.good" in g.modules()


def test_reverse_edges_and_reachability(sample: Path):
    g = rg.build_import_graph(sample)
    rev = rg.reverse_edges(g)
    assert "pkg.a" in rev["pkg.b"]          # a imports b -> a depends on b
    dep_e = rg.transitive_dependents(g, "pkg.e")
    assert {"pkg.a", "pkg.b", "pkg.c"} <= dep_e   # all reach e via a (through the cycle)
    deps_of_a = rg.transitive_dependencies(g, "pkg.a")
    assert "pkg.e" in deps_of_a


def test_resolve_target_forms(sample: Path):
    g = rg.build_import_graph(sample)
    assert rg.resolve_target(g, "pkg.a") == "pkg.a"      # module name
    assert rg.resolve_target(g, "pkg/a.py") == "pkg.a"   # repo-relative path
    assert rg.resolve_target(g, "a") == "pkg.a"          # unique leaf stem
    assert rg.resolve_target(g, "does.not.exist") is None
    assert "pkg.a" in rg.candidates(g, "a")
