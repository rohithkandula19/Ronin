"""The cached file list: walk once, re-walk when stale, never raise into the input line.

``walk_repo`` is ~180ms on this repo and ``@compaction`` is eight keystrokes, so the
caching is not an optimisation — it is the difference between a completion list and a
visibly broken input line. These tests are therefore about *how often it walks*, which
is asserted by counting calls to an injected walk rather than by timing anything.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ronin.context.fileindex import DEFAULT_TTL_SECONDS, FileIndex


def counting_walk(root: Path, names: tuple[str, ...], calls: list[int]) -> Callable[..., Any]:
    """A walk that records how many times it ran and returns ``names`` under ``root``."""

    def walk(target: Path) -> tuple[Path, ...]:
        del target
        calls.append(1)
        return tuple(root / name for name in names)

    return walk


def test_paths_come_back_repo_relative_and_posix(tmp_path: Path) -> None:
    # Absolute paths are what `walk_repo` returns and the wrong thing to show in a
    # completion list — and the wrong thing to hand a tool, which resolves relative to
    # exactly this root.
    calls: list[int] = []
    index = FileIndex(
        root=tmp_path,
        walk=counting_walk(tmp_path, ("a.py", "src/b.py"), calls),
        clock=lambda: 0.0,
    )

    assert index.paths() == ("a.py", "src/b.py")


def test_a_burst_of_typing_walks_the_tree_once(tmp_path: Path) -> None:
    calls: list[int] = []
    index = FileIndex(
        root=tmp_path, walk=counting_walk(tmp_path, ("a.py",), calls), clock=lambda: 100.0
    )

    for _keystroke in range(20):
        assert index.paths() == ("a.py",)

    assert len(calls) == 1


def test_the_snapshot_is_re_walked_once_it_is_stale(tmp_path: Path) -> None:
    calls: list[int] = []
    now = [100.0]
    index = FileIndex(
        root=tmp_path,
        walk=counting_walk(tmp_path, ("a.py",), calls),
        clock=lambda: now[0],
        ttl_seconds=5.0,
    )

    index.paths()
    now[0] = 104.9
    index.paths()
    assert len(calls) == 1, "still fresh"

    now[0] = 105.0
    index.paths()
    assert len(calls) == 2, "the ttl is inclusive at the boundary"


def test_a_file_created_during_the_session_is_offered_once_the_snapshot_expires(
    tmp_path: Path,
) -> None:
    now = [0.0]
    found = ["a.py"]

    def walk(target: Path) -> tuple[Path, ...]:
        del target
        return tuple(tmp_path / name for name in found)

    index = FileIndex(root=tmp_path, walk=walk, clock=lambda: now[0], ttl_seconds=5.0)
    assert index.paths() == ("a.py",)

    found.append("b.py")
    assert index.paths() == ("a.py",), "within the ttl, the old snapshot stands"

    now[0] = 10.0
    assert index.paths() == ("a.py", "b.py")


def test_invalidate_skips_the_wait_for_a_caller_that_knows_the_tree_moved(
    tmp_path: Path,
) -> None:
    calls: list[int] = []
    index = FileIndex(
        root=tmp_path, walk=counting_walk(tmp_path, ("a.py",), calls), clock=lambda: 0.0
    )

    index.paths()
    index.invalidate()
    index.paths()

    assert len(calls) == 2


def test_nothing_is_walked_until_something_asks(tmp_path: Path) -> None:
    # A session that never types a mention must not pay for a tree walk.
    calls: list[int] = []
    FileIndex(root=tmp_path, walk=counting_walk(tmp_path, ("a.py",), calls))

    assert calls == []


def test_a_failed_walk_keeps_the_previous_snapshot_rather_than_raising(tmp_path: Path) -> None:
    # A completion list is never worth an exception reaching the input line.
    now = [0.0]
    fail = [False]

    def walk(target: Path) -> tuple[Path, ...]:
        del target
        if fail[0]:
            raise OSError("the tree moved")
        return (tmp_path / "a.py",)

    index = FileIndex(root=tmp_path, walk=walk, clock=lambda: now[0], ttl_seconds=5.0)
    assert index.paths() == ("a.py",)

    fail[0] = True
    now[0] = 10.0
    assert index.paths() == ("a.py",)


def test_a_path_outside_the_root_is_dropped_not_raised(tmp_path: Path) -> None:
    # A symlinked walk escaping the root, or a race with a delete: drop the entry.
    def walk(target: Path) -> tuple[Path, ...]:
        del target
        return (tmp_path / "inside.py", Path("/elsewhere/outside.py"))

    index = FileIndex(root=tmp_path, walk=walk, clock=lambda: 0.0)

    assert index.paths() == ("inside.py",)


def test_the_default_ttl_is_short_enough_to_feel_live_and_long_enough_to_cache() -> None:
    assert 1.0 <= DEFAULT_TTL_SECONDS <= 30.0
