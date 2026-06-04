"""Tests for the types-gap detector and the dependency auditor."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.deps import audit, imported_top_level, parse_declared
from ronin_cli.types_gap import find_type_gaps, untyped_in_source


# ---- types ----

def test_untyped_detection() -> None:
    src = (
        "def fully(a: int) -> int:\n    return a\n"
        "def no_return(a: int):\n    return a\n"
        "def no_params(a) -> int:\n    return a\n"
        "def _private(a):\n    return a\n"
    )
    gaps = {g.symbol: g.missing for g in untyped_in_source(src)}
    assert "fully" not in gaps and "_private" not in gaps
    assert gaps["no_return"] == "return"
    assert gaps["no_params"] == "params"


def test_untyped_ignores_self_and_init_return() -> None:
    src = "class C:\n    def __init__(self, a: int):\n        self.a = a\n    def m(self, a: int) -> int:\n        return a\n"
    # __init__ excluded (private); m is fully annotated → no gaps
    assert untyped_in_source(src) == []


def test_untyped_method() -> None:
    src = "class C:\n    def go(self, a):\n        return a\n"
    gaps = untyped_in_source(src, "c.py")
    assert any(g.symbol == "C.go" for g in gaps)


def test_find_type_gaps_skips_tests(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(a):\n    return a\n", encoding="utf-8")
    (tmp_path / "test_m.py").write_text("def g(a):\n    return a\n", encoding="utf-8")
    files = {g.file for g in find_type_gaps(tmp_path)}
    assert "m.py" in files and "test_m.py" not in files


# ---- deps ----

def test_imported_top_level() -> None:
    src = "import os\nimport httpx\nfrom rich.table import Table\nfrom . import local\n"
    imp = imported_top_level(src)
    assert "httpx" in imp and "rich" in imp and "os" in imp
    assert "local" not in imp                # relative import excluded


def test_parse_declared() -> None:
    pj = '[project]\ndependencies = ["httpx>=0.2", "rich", "pydantic[email]"]\n'
    declared = parse_declared(pyproject=pj, requirements="typer\n# comment\npytest==8.0\n")
    assert "httpx" in declared and "rich" in declared and "pydantic" in declared
    assert "typer" in declared and "pytest" in declared


def test_audit_finds_undeclared_and_unused(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("import httpx\nimport os\n", encoding="utf-8")
    res = audit(tmp_path, declared={"rich"})       # declared rich, but code uses httpx
    assert "httpx" in res["undeclared"]            # used, not declared
    assert "rich" in res["unused"]                 # declared, not used
    assert "os" not in res["undeclared"]           # stdlib ignored


def test_audit_alias(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("import yaml\n", encoding="utf-8")
    # 'yaml' imports map to the 'pyyaml' distribution → declared as pyyaml = ok
    res = audit(tmp_path, declared={"pyyaml"})
    assert res["undeclared"] == [] and res["unused"] == []
