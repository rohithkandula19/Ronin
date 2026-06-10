"""Tests for `ronin mutants` — mutant generation, scoring, kill/survive runs."""
from __future__ import annotations

from ronin_cli.mutants import generate_mutants, mutation_score, run_mutants


def test_mutation_score() -> None:
    assert mutation_score(0, 0) == 1.0
    assert mutation_score(3, 4) == 0.75
    assert mutation_score(0, 2) == 0.0


def test_generate_mutants_swaps_operators() -> None:
    src = "def ok(a, b):\n    return a == b and b > 0\n"
    labels = {m.label for m in generate_mutants(src)}
    assert "== → !=" in labels
    assert "and → or" in labels
    # every mutant is the whole file with exactly one change applied
    for m in generate_mutants(src):
        assert m.mutated_source != src and m.lineno == 2


def test_generate_mutants_skips_comments_and_strings() -> None:
    # `==` lives only in a comment and a string literal → nothing to mutate.
    src = "# a == b\nmsg = 'x == y'\n"
    assert generate_mutants(src) == []


def test_generate_mutants_respects_cap() -> None:
    src = "x = True and True and True and True and True\n"
    assert len(generate_mutants(src, max_mutants=2)) == 2


def test_run_mutants_kills_with_a_strong_suite(tmp_path) -> None:
    (tmp_path / "mod.py").write_text(
        "def is_pos(n):\n    return n > 0\n", encoding="utf-8")
    (tmp_path / "test_mod.py").write_text(
        "from mod import is_pos\n"
        "def test_pos():\n    assert is_pos(5) and not is_pos(-5) and not is_pos(0)\n",
        encoding="utf-8",
    )
    res = run_mutants(tmp_path, "mod.py", "python -m pytest -q", max_mutants=10)
    assert res.total > 0
    assert res.killed == res.total          # strong test kills every mutant
    assert res.survivors == []
    # original file is restored untouched
    assert (tmp_path / "mod.py").read_text() == "def is_pos(n):\n    return n > 0\n"


def test_run_mutants_reports_survivors_with_weak_suite(tmp_path) -> None:
    (tmp_path / "mod.py").write_text(
        "def is_pos(n):\n    return n > 0\n", encoding="utf-8")
    # weak test only checks the positive case → boundary mutants survive
    (tmp_path / "test_mod.py").write_text(
        "from mod import is_pos\ndef test_pos():\n    assert is_pos(5)\n",
        encoding="utf-8",
    )
    res = run_mutants(tmp_path, "mod.py", "python -m pytest -q", max_mutants=10)
    assert res.survivors and res.score < 1.0


def test_run_mutants_bails_on_red_baseline(tmp_path) -> None:
    (tmp_path / "mod.py").write_text("x = 1 == 1\n", encoding="utf-8")
    (tmp_path / "test_mod.py").write_text(
        "def test_fail():\n    assert False\n", encoding="utf-8")
    res = run_mutants(tmp_path, "mod.py", "python -m pytest -q")
    assert "baseline" in res.note.lower() and res.total == 0
