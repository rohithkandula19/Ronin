"""Relative-import resolution in the repo map's import graph.

`_resolve_path` and `_normalize_relative` had **zero** occurrences in `tests/context/`
before this file, while `ParserUnavailable` had five — so the tree-sitter refusal was
covered and the path arithmetic underneath it was not.

This is the part with edge cases. Resolving `../../lib/util` against a source file means
walking `..` segments by hand, trying six script suffixes and an `index` file per
directory, and refusing to climb above the repo root. Get it wrong in the lenient
direction and the map grows an edge to a file outside the tree; get it wrong in the strict
direction and a real import silently vanishes from the graph — and a repo map that is
missing edges is worse than one that admits it cannot parse, because nothing looks broken.
"""

from __future__ import annotations

import pytest

from ronin.context.repomap import (
    _SCRIPT_SUFFIXES,
    ImportRef,
    RefKind,
    _normalize_relative,
    _resolve_path,
)


def ref(target: str) -> ImportRef:
    return ImportRef(kind=RefKind.PATH, target=target)


# --------------------------------------------------------------------------- #
# _normalize_relative — the segment arithmetic
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/app.ts", "src/app.ts"),
        ("src/./app.ts", "src/app.ts"),
        ("src//app.ts", "src/app.ts"),
        ("src/lib/../app.ts", "src/app.ts"),
        ("src/a/b/../../app.ts", "src/app.ts"),
        ("./src/app.ts", "src/app.ts"),
    ],
    ids=["plain", "dot", "double-slash", "one-up", "two-up", "leading-dot"],
)
def test_normalize_collapses_dot_and_parent_segments(path: str, expected: str) -> None:
    assert _normalize_relative(path) == expected


@pytest.mark.parametrize("path", ["..", "../x", "src/../../x", "a/../../.."])
def test_normalize_refuses_to_climb_above_the_root(path: str) -> None:
    """`None`, not a clamped path.

    Clamping `../../etc/passwd` to `etc/passwd` would invent an edge to a file that
    is not the one the source referred to — a wrong answer dressed as a right one.
    Refusing means the edge is simply absent, which is the honest outcome.
    """
    assert _normalize_relative(path) is None


@pytest.mark.parametrize("path", ["", ".", "./", "//"])
def test_normalize_returns_none_when_nothing_is_left(path: str) -> None:
    """An empty result is not the repo root — it is "this names no file"."""
    assert _normalize_relative(path) is None


# --------------------------------------------------------------------------- #
# _resolve_path — against a known file set
# --------------------------------------------------------------------------- #


def test_an_exact_sibling_import_resolves() -> None:
    nodes = {"src/app.ts", "src/util.ts"}
    assert _resolve_path("src/app.ts", ref("./util.ts"), nodes) == "src/util.ts"


@pytest.mark.parametrize("suffix", _SCRIPT_SUFFIXES)
def test_an_extensionless_import_resolves_through_each_script_suffix(suffix: str) -> None:
    """TypeScript and friends omit the extension; the map has to try them all.

    Parametrized over the real tuple rather than a copied list, so adding a suffix to
    `_SCRIPT_SUFFIXES` without teaching the resolver about it cannot pass silently.
    """
    target = f"src/util{suffix}"
    assert _resolve_path("src/app.ts", ref("./util"), {"src/app.ts", target}) == target


@pytest.mark.parametrize("suffix", _SCRIPT_SUFFIXES)
def test_a_directory_import_resolves_to_its_index_file(suffix: str) -> None:
    target = f"src/lib/index{suffix}"
    assert _resolve_path("src/app.ts", ref("./lib"), {"src/app.ts", target}) == target


def test_a_parent_relative_import_resolves() -> None:
    nodes = {"src/features/app.ts", "src/shared/util.ts"}
    assert (
        _resolve_path("src/features/app.ts", ref("../shared/util.ts"), nodes)
        == "src/shared/util.ts"
    )


def test_an_import_that_climbs_above_the_root_resolves_to_nothing() -> None:
    """The guard from `_normalize_relative`, reached through the caller."""
    assert _resolve_path("app.ts", ref("../../outside.ts"), {"app.ts"}) is None


def test_an_import_of_a_file_that_does_not_exist_resolves_to_nothing() -> None:
    """A dependency the map cannot see is an absent edge, never a guessed one."""
    assert _resolve_path("src/app.ts", ref("./missing"), {"src/app.ts"}) is None


def test_an_exact_match_wins_over_a_suffixed_candidate() -> None:
    """Order matters: `./util` next to both `util` and `util.ts` must take the
    literal file, or a project with extensionless scripts resolves to the wrong one."""
    nodes = {"src/app.ts", "src/util", "src/util.ts"}
    assert _resolve_path("src/app.ts", ref("./util"), nodes) == "src/util"


def test_a_file_wins_over_a_directory_index() -> None:
    """`./lib` with both `lib.ts` and `lib/index.ts` present: the file is checked
    first, matching how bundlers resolve it."""
    nodes = {"src/app.ts", "src/lib.ts", "src/lib/index.ts"}
    assert _resolve_path("src/app.ts", ref("./lib"), nodes) == "src/lib.ts"
