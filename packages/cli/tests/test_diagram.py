"""Tests for the architecture diagram (import-graph extraction + Mermaid)."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.diagram import (
    detect_package,
    import_graph,
    parse_internal_imports,
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
