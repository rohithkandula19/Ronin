"""Tests for complexity — cyclomatic counting + ranking."""
from __future__ import annotations

import ast
from pathlib import Path

from ronin_cli.complexity import (
    complexities_in_source,
    cyclomatic,
    find_complex,
    rating,
    refactor_prompt,
)


def _fn(src: str) -> ast.AST:
    return ast.parse(src).body[0]


def test_cyclomatic_straight_line_is_one() -> None:
    assert cyclomatic(_fn("def f():\n    x = 1\n    return x\n")) == 1


def test_cyclomatic_counts_branches() -> None:
    src = (
        "def f(a, b):\n"
        "    if a:\n"            # +1
        "        return 1\n"
        "    for i in b:\n"      # +1
        "        if i and a:\n"  # +1 (if) +1 (and)
        "            pass\n"
        "    return 0\n"
    )
    assert cyclomatic(_fn(src)) == 5


def test_cyclomatic_comprehension_filter() -> None:
    # base 1 + comprehension 1 + one filter 1
    assert cyclomatic(_fn("def f(xs):\n    return [x for x in xs if x]\n")) == 3


def test_complexities_qualifies_methods() -> None:
    src = (
        "class Foo:\n"
        "    def bar(self, a):\n"
        "        if a:\n"
        "            return 1\n"
        "        return 0\n"
    )
    cs = complexities_in_source(src, "m.py")
    assert len(cs) == 1
    assert cs[0].name == "Foo.bar" and cs[0].score == 2


def test_complexities_syntax_error_safe() -> None:
    assert complexities_in_source("def (((", "m.py") == []


def test_find_complex_threshold_and_order(tmp_path: Path) -> None:
    simple = "def s():\n    return 1\n"
    gnarly = "def g(a,b,c,d):\n" + "".join(
        f"    if a=={i}:\n        return {i}\n" for i in range(12))
    (tmp_path / "a.py").write_text(simple, encoding="utf-8")
    (tmp_path / "b.py").write_text(gnarly, encoding="utf-8")
    hits = find_complex(tmp_path, threshold=10)
    assert len(hits) == 1                      # 's' is below threshold
    assert hits[0].name == "g" and hits[0].score >= 12


def test_rating_bands() -> None:
    assert rating(1) == "simple"
    assert rating(7) == "moderate"
    assert rating(12) == "high"
    assert rating(25) == "very high"
    assert rating(40) == "untestable"


def test_refactor_prompt_mentions_target() -> None:
    from ronin_cli.complexity import FuncComplexity
    p = refactor_prompt(FuncComplexity("big", "x.py", 9, 22))
    assert "big" in p and "x.py" in p and "22" in p
