"""Tests for the code-smell detector."""
from __future__ import annotations

from ronin_cli.smell import Thresholds, detect_smells


def _kinds(source: str, **kw) -> set[str]:
    t = Thresholds(**kw) if kw else None
    return {s.kind for s in detect_smells(source, t)}


def test_clean_code_has_no_smells() -> None:
    src = "def add(a, b):\n    return a + b\n"
    assert detect_smells(src) == []


def test_too_many_params() -> None:
    src = "def f(a, b, c, d, e, f, g):\n    return 1\n"
    assert "too-many-params" in _kinds(src)


def test_param_threshold_respected() -> None:
    src = "def f(a, b, c):\n    return 1\n"
    assert "too-many-params" in _kinds(src, max_params=2)
    assert "too-many-params" not in _kinds(src, max_params=3)


def test_deep_nesting_is_high_severity() -> None:
    src = (
        "def f(x):\n"
        "    if x:\n"
        "        for i in x:\n"
        "            while i:\n"
        "                with open('p') as fh:\n"
        "                    if fh:\n"
        "                        return 1\n"
    )
    smells = detect_smells(src)
    nesting = [s for s in smells if s.kind == "deep-nesting"]
    assert nesting and nesting[0].severity == "high"


def test_bare_except_is_high() -> None:
    src = "try:\n    x = 1\nexcept:\n    pass\n"
    smells = [s for s in detect_smells(src) if s.kind == "broad-except"]
    assert smells and smells[0].severity == "high"


def test_broad_exception_is_warn() -> None:
    src = "try:\n    x = 1\nexcept Exception:\n    pass\n"
    smells = [s for s in detect_smells(src) if s.kind == "broad-except"]
    assert smells and smells[0].severity == "warn"


def test_many_returns() -> None:
    body = "\n".join(f"    if x == {i}: return {i}" for i in range(8))
    src = f"def f(x):\n{body}\n"
    assert "many-returns" in _kinds(src)


def test_nested_function_not_double_counted() -> None:
    # the inner function's params must not inflate the outer function's count
    src = (
        "def outer(a):\n"
        "    def inner(p, q, r, s, t, u, v):\n"
        "        return p\n"
        "    return inner\n"
    )
    smells = detect_smells(src)
    params = [s for s in smells if s.kind == "too-many-params"]
    assert len(params) == 1 and params[0].function == "inner"


def test_syntax_error_returns_parse_error() -> None:
    smells = detect_smells("def f(:\n")
    assert len(smells) == 1 and smells[0].kind == "parse-error"


def test_results_sorted_by_line() -> None:
    src = "def f(a, b, c, d, e, f, g):\n    return 1\n\n\ndef g(a, b, c, d, e, f, g, h):\n    return 2\n"
    smells = detect_smells(src)
    lines = [s.line for s in smells]
    assert lines == sorted(lines)
