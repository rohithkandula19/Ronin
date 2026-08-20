"""Tests for autonomous bisect — parser + a real git bisect round-trip."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ronin_cli.bisect import default_good, parse_first_bad, run_bisect


def test_parse_first_bad() -> None:
    out = "Bisecting: 3 revisions left\nabc1234 is the first bad commit\ncommit abc1234"
    assert parse_first_bad(out) == "abc1234"
    assert parse_first_bad("nothing here") is None


def test_parse_first_bad_full_sha() -> None:
    sha = "a" * 40
    assert parse_first_bad(f"{sha} is the first bad commit") == sha


def _git(root: Path, *a: str, **kw) -> str:
    return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True,
                          check=kw.get("check", True)).stdout


def test_run_bisect_finds_the_breaking_commit(tmp_path: Path) -> None:
    """A real git bisect round trip, on every platform.

    This used to be skipped on macOS, blamed on "the throwaway checkout doesn't
    surface value.txt as expected on macOS". That diagnosis was wrong: the harness was
    fine and bisect was succeeding, but `parse_first_bad` could not read git's answer
    because newer git quotes the term name (`is the first 'bad' commit`). macOS
    runners simply shipped that git first. The skip is gone with the cause.
    """
    # build a repo: good commits, then a commit that breaks a marker file, then more
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    # the "test" passes only when value.txt contains the marker PASS
    (tmp_path / "test.sh").write_text('grep -q PASS value.txt\n', encoding="utf-8")
    (tmp_path / "value.txt").write_text("PASS\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "good 1")
    (tmp_path / "x.txt").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "good 2")
    # the breaking commit (FAIL does not contain PASS)
    (tmp_path / "value.txt").write_text("FAIL\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "BREAK the value")
    bad_sha = _git(tmp_path, "rev-parse", "HEAD").strip()
    (tmp_path / "y.txt").write_text("b\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "after break")

    good = _git(tmp_path, "rev-list", "--max-parents=0", "HEAD").strip()
    res = run_bisect(tmp_path, "sh test.sh", good=good, bad="HEAD")
    assert res.culprit is not None, res.note
    # the culprit must be the BREAK commit
    assert bad_sha.startswith(res.culprit) or res.culprit in bad_sha
    assert "BREAK" in res.subject
    # the user's working tree is untouched (bisect ran in a throwaway clone)
    assert (tmp_path / "value.txt").read_text() == "FAIL\n"


@pytest.mark.parametrize(
    "line",
    [
        # git <= 2.43
        "4bd85ea9640af8c3c8f479264b4662f8cec0cab8 is the first bad commit",
        # git 2.55 quotes the term, which is what broke this in CI
        "4bd85ea9640af8c3c8f479264b4662f8cec0cab8 is the first 'bad' commit",
        # `git bisect start --term-new=...`, and the old/new spelling
        "4bd85ea9640af8c3c8f479264b4662f8cec0cab8 is the first new commit",
        "4bd85ea9640af8c3c8f479264b4662f8cec0cab8 is the first 'broken' commit",
        # case, which was already tolerated
        "4bd85ea9640af8c3c8f479264b4662f8cec0cab8 IS THE FIRST BAD COMMIT",
    ],
)
def test_every_spelling_of_gits_answer_is_understood(line: str) -> None:
    """The term is quoted on newer git and renameable on any git.

    Getting this wrong does not look like a parse bug from the outside: bisect
    succeeds, the sha is right there in the output, and `ronin bisect` reports that it
    could not isolate a commit.
    """
    assert parse_first_bad(line) == "4bd85ea9640af8c3c8f479264b4662f8cec0cab8"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "nothing to see here",
        # No term at all is not git's format, and matching it would let a stray sha in.
        "4bd85ea9640af8c3c8f479264b4662f8cec0cab8 is the first commit",
        "not a sha is the first bad commit",
    ],
)
def test_text_that_is_not_gits_answer_is_rejected(line: str) -> None:
    assert parse_first_bad(line) is None


def test_default_good_uses_tag(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a").write_text("1", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    _git(tmp_path, "tag", "v1.0")
    assert default_good(tmp_path) == "v1.0"


def test_a_failure_carries_gits_own_words_not_only_a_guess(tmp_path: Path) -> None:
    """A bisect that finds nothing must say what git said.

    This test exists because of a CI failure that could not be reproduced locally:
    the note named the most likely cause and discarded the evidence, so three
    different root causes were indistinguishable in the log.
    """
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "value.txt").write_text("PASS\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "only commit")
    good = _git(tmp_path, "rev-parse", "HEAD").strip()

    # A second commit, so `bisect start` has a range to work with, and a command that
    # passes at both ends — so bisect can never name a first bad commit.
    (tmp_path / "x.txt").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "second")

    res = run_bisect(tmp_path, "true", good=good, bad="HEAD")
    assert res.culprit is None
    assert "couldn't isolate" in res.note
    assert "git exited" in res.note, "the exit code is part of the evidence"
    # The guess survives, but as a guess rather than as the whole message.
    assert "Most often" in res.note


def test_a_note_that_would_be_enormous_is_cut_with_a_marker() -> None:
    from ronin_cli.bisect import NOTE_OUTPUT_CHARS, _no_culprit_note

    class _Run:
        returncode = 1
        stdout = ""
        stderr = "x" * (NOTE_OUTPUT_CHARS + 500)

    note = _no_culprit_note(_Run())  # type: ignore[arg-type]
    assert "more chars" in note
    assert len(note) < NOTE_OUTPUT_CHARS + 400


def test_a_silent_git_is_reported_as_silent_rather_than_as_blank() -> None:
    """The bug this closes: a note that ended in a colon and said nothing."""
    from ronin_cli.bisect import _git_note, _no_culprit_note

    class _Run:
        returncode = 2
        stdout = ""
        stderr = ""

    assert "printed nothing" in _no_culprit_note(_Run())  # type: ignore[arg-type]
    note = _git_note("bisect start failed.", _Run())  # type: ignore[arg-type]
    assert not note.rstrip().endswith(":")
    assert "git exited 2" in note


def test_run_bisect_outside_git(tmp_path: Path) -> None:
    res = run_bisect(tmp_path, "true", good="x")
    assert res.culprit is None and "git" in res.note.lower()
