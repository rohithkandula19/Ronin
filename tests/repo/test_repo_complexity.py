"""Cyclomatic complexity — and the two counting bugs the v1 version had.

Both were confirmed against the shipped `ronin_cli.complexity` before anything was
changed, and both are pinned here, because both are the kind of bug that makes a measure
*look* like it works. Nothing crashes, every number is plausible, and the ranking is
quietly wrong:

* `with` counted as a branch inflated every function that touches a file, a lock or a
  transaction — which is most of them — and a measure that is high everywhere ranks
  nothing;
* `ast.walk` descending into nested `def`s counted an inner function's branches twice and
  attributed them to a function that did not contain any, so the reader was sent to fix
  the wrong function.

Everything here is a string in and a number out. No filesystem.
"""

from __future__ import annotations

import pytest

from ronin.repo.complexity import (
    DEFAULT_THRESHOLD,
    Complexity,
    complexity_of,
    cyclomatic,
    rank,
    rating,
)


def _score(source: str, name: str = "") -> int:
    found = complexity_of(source, "t.py")
    if name:
        return next(item.score for item in found if item.name == name)
    return found[0].score


# --------------------------------------------------------------------------- #
# the baseline
# --------------------------------------------------------------------------- #


def test_a_straight_line_function_is_one() -> None:
    """One path in, one path out. Everything else is measured against this."""
    assert _score("def f():\n    return 1\n") == 1


@pytest.mark.parametrize(
    "body",
    [
        "    if x:\n        pass\n",
        "    for i in x:\n        pass\n",
        "    while x:\n        pass\n",
        "    y = 1 if x else 2\n",
        "    assert x\n",
        "    try:\n        pass\n    except ValueError:\n        pass\n",
    ],
)
def test_each_decision_point_adds_exactly_one(body: str) -> None:
    assert _score(f"def f(x):\n{body}") == 2


def test_an_async_loop_counts_like_a_loop() -> None:
    assert _score("async def f(x):\n    async for i in x:\n        pass\n") == 2


def test_each_extra_boolean_operand_is_another_way_out() -> None:
    """`a and b and c` can leave at three points, not two."""
    assert _score("def f(a, b):\n    return a and b\n") == 2
    assert _score("def f(a, b, c):\n    return a and b and c\n") == 3


def test_a_comprehension_counts_its_loop_and_each_filter() -> None:
    assert _score("def f(xs):\n    return [x for x in xs]\n") == 2
    assert _score("def f(xs):\n    return [x for x in xs if x]\n") == 3
    assert _score("def f(xs):\n    return [x for x in xs if x if x > 1]\n") == 4


def test_a_match_case_is_a_branch() -> None:
    source = (
        "def f(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            pass\n"
        "        case _:\n"
        "            pass\n"
    )
    assert _score(source) == 3


# --------------------------------------------------------------------------- #
# bug one: `with` is not a branch
# --------------------------------------------------------------------------- #


def test_a_with_block_is_not_a_decision_point() -> None:
    """v1 scored these 2 and 1. They are the same logic and the same number of paths.

    A `with` binds and unbinds; it does not choose. Counting it inflates every function
    that opens a file, takes a lock or starts a transaction.
    """
    managed = "def f(p):\n    with open(p) as handle:\n        return handle.read()\n"
    plain = "def f(p):\n    handle = open(p)\n    return handle.read()\n"
    assert _score(managed) == _score(plain) == 1


def test_an_async_with_is_not_one_either() -> None:
    assert _score("async def f(lock):\n    async with lock:\n        return 1\n") == 1


def test_nesting_context_managers_does_not_accumulate() -> None:
    """The shape that made the bug matter: three managers, still no branch."""
    source = (
        "def f(a, b, c):\n"
        "    with a:\n"
        "        with b:\n"
        "            with c:\n"
        "                return 1\n"
    )
    assert _score(source) == 1


def test_a_with_that_really_does_branch_still_counts_the_branch() -> None:
    """Guarding against over-correcting: the `if` inside is still an `if`."""
    source = (
        "def f(p, x):\n"
        "    with open(p) as handle:\n"
        "        if x:\n"
        "            return handle\n"
        "    return None\n"
    )
    assert _score(source) == 2


# --------------------------------------------------------------------------- #
# bug two: a nested function's branches are its own
# --------------------------------------------------------------------------- #


def test_a_factory_does_not_inherit_its_inner_functions_branches() -> None:
    """v1 scored `outer` 2. `outer` contains no branch at all.

    The consequence is worse than the number: the reader is sent to simplify a function
    that is already as simple as a function gets.
    """
    source = (
        "def outer():\n"
        "    def inner(x):\n"
        "        if x:\n"
        "            return 1\n"
        "        return 2\n"
        "    return inner\n"
    )
    scores = {item.name: item.score for item in complexity_of(source, "t.py")}
    assert scores == {"outer": 1, "inner": 2}


def test_the_branch_is_counted_once_across_the_whole_file() -> None:
    """v1 counted it twice — once against the nested function and once against its
    parent — so the file's total was wrong as well as its attribution."""
    source = (
        "def outer():\n    def inner(x):\n        if x:\n            return 1\n    return inner\n"
    )
    total = sum(item.score for item in complexity_of(source, "t.py"))
    assert total == 1 + 2


def test_a_lambda_is_a_separate_callable_and_not_the_enclosing_functions_branch() -> None:
    """Same rule as a nested `def`. A lambda has no name to report under, so it is
    excluded rather than measured — but it must not inflate whatever encloses it."""
    assert _score("def f(xs):\n    return sorted(xs, key=lambda x: 1 if x else 2)\n") == 1


def test_a_deeply_nested_definition_belongs_to_its_immediate_parent() -> None:
    source = (
        "def a():\n"
        "    def b():\n"
        "        def c(x):\n"
        "            if x:\n"
        "                return 1\n"
        "        return c\n"
        "    return b\n"
    )
    scores = {item.name: item.score for item in complexity_of(source, "t.py")}
    assert scores == {"a": 1, "b": 1, "c": 2}


# --------------------------------------------------------------------------- #
# naming, so a report can be read without opening the file
# --------------------------------------------------------------------------- #


def test_a_method_is_reported_under_its_class() -> None:
    source = "class Widget:\n    def render(self, x):\n        if x:\n            return 1\n"
    (found,) = complexity_of(source, "w.py")
    assert found.name == "Widget.render"


def test_a_function_defined_inside_a_method_is_not_that_classs_method() -> None:
    """Only direct children of the class body get the prefix — `helper` is not
    `Widget.helper`, and calling it that would send somebody looking for a method that
    does not exist."""
    source = (
        "class Widget:\n"
        "    def render(self):\n"
        "        def helper(x):\n"
        "            if x:\n"
        "                return 1\n"
        "        return helper\n"
    )
    names = {item.name for item in complexity_of(source, "w.py")}
    assert names == {"Widget.render", "helper"}


def test_a_finding_carries_its_path_and_line() -> None:
    (found,) = complexity_of("\n\ndef f():\n    return 1\n", "pkg/mod.py")
    assert found == Complexity(name="f", path="pkg/mod.py", line=3, score=1)


# --------------------------------------------------------------------------- #
# ratings and ranking
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (1, "simple"),
        (5, "simple"),
        (6, "moderate"),
        (9, "moderate"),
        (10, "high"),
        (19, "high"),
        (20, "very high"),
        (29, "very high"),
        (30, "untestable"),
        (200, "untestable"),
    ],
)
def test_the_rating_boundaries(score: int, label: str) -> None:
    assert rating(score) == label


def test_the_default_threshold_is_where_high_starts() -> None:
    """A report of everything is a report nobody reads."""
    assert rating(DEFAULT_THRESHOLD) == "high"
    assert rating(DEFAULT_THRESHOLD - 1) != "high"


def test_ranking_is_worst_first_then_stable_by_path_and_line() -> None:
    simple = "def a():\n    return 1\n"
    branchy = "def b(x, y):\n    if x:\n        pass\n    if y:\n        pass\n"
    ranked = rank({"z.py": branchy, "a.py": simple}, threshold=1)
    assert [(item.name, item.score) for item in ranked] == [("b", 3), ("a", 1)]


def test_the_threshold_excludes_and_the_limit_truncates() -> None:
    sources = {f"m{index}.py": "def f(x):\n    if x:\n        pass\n" for index in range(5)}
    assert len(rank(sources, threshold=2)) == 5
    assert rank(sources, threshold=3) == ()
    assert len(rank(sources, threshold=2, limit=2)) == 2


def test_rank_takes_pairs_as_well_as_a_mapping() -> None:
    """Pairs, so the caller's tree walk can stream rather than build a dict of every
    file's text at once."""
    pairs = [("a.py", "def f(x):\n    if x:\n        pass\n")]
    assert rank(pairs, threshold=1)[0].name == "f"


# --------------------------------------------------------------------------- #
# what a broken file does
# --------------------------------------------------------------------------- #


def test_a_file_that_does_not_parse_yields_nothing_rather_than_raising() -> None:
    """Advisory, not a gate: one syntax error must not deny the reader the rest of
    the tree."""
    assert complexity_of("def f(:\n", "broken.py") == ()


def test_one_broken_file_does_not_stop_the_ranking() -> None:
    ranked = rank({"broken.py": "def f(:", "ok.py": "def g(x):\n    if x:\n        pass\n"})
    assert [
        item.name for item in rank({"ok.py": "def g(x):\n    if x:\n        pass\n"}, threshold=1)
    ] == ["g"]
    assert ranked == ()  # `g` scores 2, below the default threshold of 10


def test_an_empty_file_is_not_a_special_case() -> None:
    assert complexity_of("", "empty.py") == ()


def test_cyclomatic_can_be_called_on_one_node() -> None:
    """The public seam a caller with its own AST would use."""
    import ast

    tree = ast.parse("def f(x):\n    if x:\n        pass\n")
    assert cyclomatic(tree.body[0]) == 2


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
