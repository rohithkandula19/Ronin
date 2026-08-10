"""The search tools' remaining branches: paths outside the root, a vanished file, -A/-B.

`test_search.py` drives a real `rg` against a real tree, which is the right call —
the reason to wrap ripgrep rather than reimplement it is its behaviour, and a mock
would assert our idea of that behaviour. But it skips its whole module when ripgrep
is absent, and four branches were never reached:

* a match path that cannot be made relative to the root
* `glob` pointed at something that is not a directory
* a file that disappears between being listed and being stat'd
* grep's `-A` / `-B` context flags

The first and third are the interesting ones. Both are cases where the honest
behaviour is to degrade rather than fail: a search should not blow up because one
file vanished underneath it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from harness import call, context, write_file

from ronin.tools import GlobTool, GrepTool
from ronin.tools.search import _relative_to

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")

GLOB = GlobTool()
GREP = GrepTool()


# --------------------------------------------------------------------------- #
# _relative_to — no ripgrep needed
# --------------------------------------------------------------------------- #


def test_a_path_under_the_root_is_shortened(tmp_path: Path) -> None:
    """The normal case: every match line drops the shared prefix, which is pure
    token cost for a model that already knows where it is."""
    assert _relative_to(tmp_path / "src" / "app.py", tmp_path) == "src/app.py"


def test_the_root_itself_is_reported_as_dot(tmp_path: Path) -> None:
    assert _relative_to(tmp_path, tmp_path) == "."


def test_a_path_outside_the_root_is_returned_absolute(tmp_path: Path) -> None:
    """`relative_to` raises for a path that is not under the root, and the honest
    answer is the absolute path.

    The tempting alternative — computing `../../..` — would produce a path that
    reads as though it were inside the project. A long absolute path is uglier and
    truthful, which is the right trade for something the model may act on.
    """
    outside = Path("/etc/passwd")
    assert _relative_to(outside, tmp_path) == "/etc/passwd"


def test_a_sibling_directory_is_not_reported_as_relative(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    sibling = tmp_path / "elsewhere" / "notes.md"
    assert _relative_to(sibling, root) == str(sibling)
    assert not _relative_to(sibling, root).startswith("..")


# --------------------------------------------------------------------------- #
# glob against something that is not a directory
# --------------------------------------------------------------------------- #


@needs_rg
async def test_glob_refuses_a_path_that_is_a_file(tmp_path: Path) -> None:
    """Passing a file where a directory belongs is a plausible model mistake, and
    ripgrep's own error for it is far less clear than saying which path was wrong."""
    write_file(tmp_path, "src/app.py", "x = 1\n")
    result = await call(GLOB, context(tmp_path), pattern="*.py", path="src/app.py")
    assert not result.ok
    assert "not a directory" in (result.error or "")


@needs_rg
async def test_glob_refuses_a_path_that_does_not_exist(tmp_path: Path) -> None:
    result = await call(GLOB, context(tmp_path), pattern="*.py", path="nowhere")
    assert not result.ok
    assert "not a directory" in (result.error or "")


# --------------------------------------------------------------------------- #
# A file that vanishes between listing and stat
# --------------------------------------------------------------------------- #


@needs_rg
async def test_a_file_that_vanishes_mid_search_does_not_fail_the_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`glob` sorts by mtime, so it stats every name ripgrep returned — and in a
    tree being written to by a build, a file can be gone by then.

    Dropping it to the bottom of the ordering is right: losing the sort position of
    one file is a rounding error, while failing the whole search because a
    `node_modules` entry moved would make the tool untrustworthy exactly when the
    repo is busy. Simulated rather than raced, so the test is deterministic.
    """
    write_file(tmp_path, "keep.py", "x = 1\n")
    write_file(tmp_path, "vanishes.py", "y = 2\n")

    real_stat = Path.stat

    def flaky_stat(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == "vanishes.py":
            raise OSError("No such file or directory")
        return real_stat(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", flaky_stat)

    result = await call(GLOB, context(tmp_path), pattern="*.py")
    assert result.ok, result.error
    assert "keep.py" in result.content
    assert "vanishes.py" in result.content, "the file was dropped, not just re-ordered"


# --------------------------------------------------------------------------- #
# grep's context flags
# --------------------------------------------------------------------------- #


@needs_rg
@pytest.mark.parametrize(("flag", "neighbour"), [("-A", "after = 1"), ("-B", "before = 1")])
async def test_grep_after_and_before_context_reach_ripgrep(
    tmp_path: Path, flag: str, neighbour: str
) -> None:
    """`-A`/`-B` are the flags that make a match readable — a hit with no
    surrounding lines often cannot be judged without a second read call.

    Asserted through the real `rg`, so this checks the flag is actually wired to
    `--after-context`/`--before-context` and not merely accepted and dropped.
    """
    write_file(tmp_path, "a.py", "before = 1\nNEEDLE\nafter = 1\n")
    result = await call(GREP, context(tmp_path), pattern="NEEDLE", **{flag: 1})
    assert result.ok, result.error
    assert "NEEDLE" in result.content
    assert neighbour in result.content, f"{flag} did not bring its context line"


@needs_rg
async def test_grep_without_context_flags_returns_only_the_match(tmp_path: Path) -> None:
    """The control for the pair above: without `-A`/`-B` the neighbours stay out,
    so the assertions there are testing the flags rather than the file."""
    write_file(tmp_path, "a.py", "before = 1\nNEEDLE\nafter = 1\n")
    result = await call(GREP, context(tmp_path), pattern="NEEDLE")
    assert result.ok, result.error
    assert "NEEDLE" in result.content
    assert "before = 1" not in result.content
    assert "after = 1" not in result.content
