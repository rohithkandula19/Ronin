"""Tests for symbol_lens — definition + call-site location."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.symbol_lens import (
    definitions_in_source,
    explain_brief,
    explain_prompt,
    locate,
    references_in_source,
)


def test_definitions_function_class_method() -> None:
    src = (
        "def foo():\n    pass\n"
        "class Bar:\n    def foo(self):\n        pass\n"
    )
    foo = definitions_in_source(src, "foo", "m.py")
    kinds = sorted(d.kind for d in foo)
    assert kinds == ["function", "method"]
    bar = definitions_in_source(src, "Bar", "m.py")
    assert len(bar) == 1 and bar[0].kind == "class"


def test_definitions_syntax_error_safe() -> None:
    assert definitions_in_source("def (((", "x", "m.py") == []


def test_references_excludes_def_and_imports() -> None:
    src = (
        "from pkg import foo\n"      # import — skipped
        "def foo():\n"               # def line — skipped via def_lines
        "    return 1\n"
        "x = foo()\n"                # real call site
        "y = foobar()\n"            # word-boundary: 'foobar' != 'foo'
    )
    sites = references_in_source(src, "foo", "m.py", def_lines={2})
    assert len(sites) == 1
    assert sites[0].line == 4 and "foo()" in sites[0].text


def test_locate_across_tree(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    (tmp_path / "use.py").write_text("from core import handler\nx = handler()\n", encoding="utf-8")
    (tmp_path / "skip.py").write_text("y = 0  # no mention here\n", encoding="utf-8")
    loc = locate(tmp_path, "handler")
    files_def = {d.file for d in loc["definitions"]}
    assert "core.py" in files_def
    assert loc["total_sites"] == 1
    assert loc["call_sites"][0].file == "use.py"


def test_explain_brief_and_prompt() -> None:
    loc = locate.__wrapped__ if hasattr(locate, "__wrapped__") else None  # noqa: F841
    fake = {
        "definitions": [type("D", (), {"kind": "function", "file": "a.py", "line": 3})()],
        "call_sites": [type("C", (), {"file": "b.py", "line": 7, "text": "a()"})()],
        "total_sites": 1,
    }
    brief = explain_brief(fake, "a")
    assert "a.py:3" in brief and "b.py:7" in brief and "1 call site" in brief
    p = explain_prompt(fake, "a")
    assert "`a`" in p and "contract" in p.lower()


def test_locate_missing_symbol(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    loc = locate(tmp_path, "nonexistent")
    assert loc["definitions"] == [] and loc["total_sites"] == 0
