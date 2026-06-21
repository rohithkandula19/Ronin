"""Tests for the architecture diagram (import-graph extraction + Mermaid)."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.diagram import (
    build_graph,
    detect_package,
    generate,
    import_graph,
    package_of,
    parse_internal_imports,
    scan_imports,
    stats,
    to_mermaid,
)


def test_parse_internal_imports() -> None:
    known = {"foo", "bar", "baz"}
    text = "from .foo import x\nfrom . import bar\nimport os\nfrom .nope import y\n"
    assert parse_internal_imports(text, known) == {"foo", "bar"}


def test_import_graph(tmp_path: Path) -> None:
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text("from .b import thing\nfrom .c import other\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from .c import other\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("x = 1\n", encoding="utf-8")
    g = import_graph(tmp_path)
    assert g["a"] == {"b", "c"} and g["b"] == {"c"} and g["c"] == set()


def test_to_mermaid() -> None:
    mer = to_mermaid({"a": {"b"}, "b": set()})
    assert "```mermaid" in mer and "graph LR" in mer
    assert "a --> b" in mer


def test_to_mermaid_isolated_node() -> None:
    mer = to_mermaid({"lonely": set()})
    assert "lonely" in mer


def test_stats() -> None:
    g = {"a": {"c"}, "b": {"c"}, "c": set()}
    s = stats(g)
    assert s["modules"] == 3 and s["edges"] == 2
    assert s["most_depended"][0] == "c"      # both a and b depend on c
    assert "c" in s["leaves"]                # c imports nothing


def test_detect_package(tmp_path: Path) -> None:
    small = tmp_path / "small"
    small.mkdir()
    (small / "__init__.py").write_text("", encoding="utf-8")
    (small / "x.py").write_text("", encoding="utf-8")
    big = tmp_path / "big"
    big.mkdir()
    (big / "__init__.py").write_text("", encoding="utf-8")
    for n in "abc":
        (big / f"{n}.py").write_text("", encoding="utf-8")
    assert detect_package(tmp_path) == big   # the one with more modules


# ---------------------------------------------------------------------------
# repo-wide pipeline: build_graph / to_mermaid(direction) / package_of /
# scan_imports / generate
# ---------------------------------------------------------------------------
def test_build_graph_dedupes_and_keeps_internal_only() -> None:
    # 'a' imports 'b' twice (set already de-dupes) plus the external 'os';
    # only 'b' is an internal module (a key in the input), so the 'os' edge drops.
    imports = {"a": {"b", "os"}, "b": set()}
    g = build_graph(imports)
    assert g["nodes"] == ["a", "b"]            # de-duped, sorted, internal nodes
    assert g["edges"] == [("a", "b")]          # external 'os' edge excluded
    # self-edges are dropped, and the (a,b) pair appears exactly once
    g2 = build_graph({"a": {"a", "b"}, "b": {"a"}})
    assert ("a", "a") not in g2["edges"]
    assert sorted(g2["edges"]) == [("a", "b"), ("b", "a")]


def test_to_mermaid_graph_lr_and_edge_line() -> None:
    g = build_graph({"a": {"b"}, "b": set()})
    mer = to_mermaid(g)
    assert "graph LR" in mer
    assert "a --> b" in mer


def test_to_mermaid_direction_td() -> None:
    mer = to_mermaid(build_graph({"a": {"b"}, "b": set()}), direction="TD")
    assert "graph TD" in mer and "graph LR" not in mer


def test_to_mermaid_sanitizes_dotted_id() -> None:
    # a dotted module name is not a legal Mermaid id → underscored, label kept
    g = build_graph({"pkg.a": {"pkg.b"}, "pkg.b": set()})
    mer = to_mermaid(g)
    assert "pkg.a -->" not in mer               # raw dotted id must not appear as id
    assert "pkg_a --> pkg_b" in mer             # sanitized edge
    assert 'pkg_a["pkg.a"]' in mer              # original name preserved as label


def test_to_mermaid_accepts_raw_import_graph() -> None:
    # backward-compat: passing the raw {module: deps} mapping still works
    mer = to_mermaid({"a": {"b"}, "b": set()})
    assert "graph LR" in mer and "a --> b" in mer


def test_package_of() -> None:
    assert package_of("pkg/sub/mod.py") == "pkg"
    assert package_of("pkg.sub.mod") == "pkg"
    assert package_of("solo") == "solo"
    assert package_of("/abs/pkg/x") == "abs"


def test_scan_imports_python_internal_only(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "import os\nfrom b import thing\nimport sys\n", encoding="utf-8"
    )
    (tmp_path / "b.py").write_text("x = 1\n", encoding="utf-8")
    g = scan_imports(tmp_path)
    assert g["a"] == {"b"}        # 'os'/'sys' are external → dropped
    assert g["b"] == set()


def test_scan_imports_jsts_relative(tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_text(
        "import {f} from './util';\nimport React from 'react';\n", encoding="utf-8"
    )
    (tmp_path / "util.ts").write_text("export const f = 1;\n", encoding="utf-8")
    g = scan_imports(tmp_path)
    assert g["app"] == {"util"}   # './util' internal; 'react' external → dropped


def test_generate_writes_file_no_network(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("from b import x\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 1\n", encoding="utf-8")
    out = tmp_path / "architecture.md"
    text = generate(tmp_path, out=str(out))
    assert "graph LR" in text and "a --> b" in text
    assert out.exists()
    written = out.read_text(encoding="utf-8")
    assert "```mermaid" in written and "a --> b" in written
