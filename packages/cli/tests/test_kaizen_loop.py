"""Tests for the continuous-Kaizen PURE decision core.

``run_continuous`` runs a live cycle (worktree fix + test suite) and shells out to
git/gh, so it isn't unit-tested here. The objective gate that decides whether a
proven diff is worth a PR — ``should_open_pr`` — and the diff-stat helper that
feeds it are pure, so those are what we lock down.
"""
from __future__ import annotations

from ronin_cli.kaizen_loop import _diff_stats, should_open_pr


# --------------------------------------------------------------------------
# should_open_pr — the objective "PR-worthy?" gate.
# --------------------------------------------------------------------------

def test_should_open_pr_happy_path() -> None:
    # tests pass + a real change → open a PR
    assert should_open_pr(True, 1, 5) is True
    assert should_open_pr(True, 3, 40) is True


def test_should_open_pr_requires_passing_tests() -> None:
    # red fitness gate → never, regardless of how big the change is
    assert should_open_pr(False, 1, 5) is False
    assert should_open_pr(False, 10, 500) is False


def test_should_open_pr_rejects_no_files() -> None:
    assert should_open_pr(True, 0, 0) is False
    assert should_open_pr(True, 0, 50) is False  # lines but no files = malformed/no-op


def test_should_open_pr_rejects_trivial_line_count() -> None:
    # a 1-line "diff" is almost always a no-op the gate happened to pass
    assert should_open_pr(True, 1, 1) is False


def test_should_open_pr_boundary_is_two_lines() -> None:
    # exactly the minimum (1 file, 2 lines) is accepted; one less is not
    assert should_open_pr(True, 1, 2) is True
    assert should_open_pr(True, 1, 1) is False


def test_should_open_pr_returns_bool() -> None:
    # callers branch on this; make sure it's a real bool, not a truthy int
    for args in [(True, 1, 5), (False, 1, 5), (True, 0, 0)]:
        assert isinstance(should_open_pr(*args), bool)


# --------------------------------------------------------------------------
# _diff_stats — pure (files_changed, lines_changed) from a unified diff.
# --------------------------------------------------------------------------

def test_diff_stats_empty() -> None:
    assert _diff_stats("") == (0, 0)
    assert _diff_stats("   \n  \n") == (0, 0)


def test_diff_stats_counts_files_and_lines() -> None:
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "index 111..222 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,3 @@\n"
        " unchanged\n"
        "-old line\n"
        "+new line\n"
        "+extra added\n"
    )
    files, lines = _diff_stats(diff)
    assert files == 1
    # one '-' body line + two '+' body lines; +++/--- headers excluded
    assert lines == 3


def test_diff_stats_counts_multiple_files() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-p\n+q\n"
    )
    files, lines = _diff_stats(diff)
    assert files == 2
    assert lines == 4  # two changes per file, headers excluded


def test_diff_stats_gate_composition() -> None:
    # the two pure pieces compose into the real decision the loop makes
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -1 +1,2 @@\n-a\n+a\n+b\n"
    )
    files, lines = _diff_stats(diff)
    assert should_open_pr(True, files, lines) is True
    assert should_open_pr(False, files, lines) is False
