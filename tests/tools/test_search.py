"""glob, grep, ls against a real temp tree and a real ripgrep.

These run the actual ``rg`` binary rather than mocking it, because the reason to wrap
ripgrep instead of reimplementing search is its behaviour — gitignore handling, type
filters, multiline mode — and a mock would assert on our idea of that behaviour rather
than the real thing.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from harness import call, context, write_file

from ronin.tools import GlobTool, GrepTool, LsTool

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")

GLOB = GlobTool()
GREP = GrepTool()
LS = LsTool()


def tree(root: Path) -> None:
    write_file(root, "src/app.py", "def handler():\n    return None\n")
    write_file(root, "src/util.py", "def helper():\n    return None\n\n# TODO: tidy\n")
    write_file(root, "tests/test_app.py", "def test_handler():\n    assert True\n")
    write_file(root, "README.md", "# Project\n\nSome prose about handler().\n")
    write_file(root, "build/generated.py", "# do not edit\nhandler = 1\n")
    write_file(root, ".gitignore", "build/\n")


# --------------------------------------------------------------------------- #
# glob
# --------------------------------------------------------------------------- #


async def test_glob_finds_files_by_pattern(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GLOB, context(tmp_path), pattern="**/*.py")
    assert result.ok
    assert "src/app.py" in result.content
    assert "tests/test_app.py" in result.content


async def test_paths_come_back_without_a_dot_slash_prefix(tmp_path: Path) -> None:
    """The model feeds these straight back to read; `./` is noise it has to strip."""
    tree(tmp_path)
    globbed = await call(GLOB, context(tmp_path), pattern="**/*.py")
    grepped = await call(GREP, context(tmp_path), pattern="handler", mode="files")
    assert "./" not in globbed.content
    assert "./" not in grepped.content


async def test_glob_respects_gitignore(tmp_path: Path) -> None:
    """The reason to wrap ripgrep: build/ is ignored without us knowing about it."""
    tree(tmp_path)
    result = await call(GLOB, context(tmp_path), pattern="**/*.py")
    assert "build/generated.py" not in result.content


async def test_glob_sorts_newest_first(tmp_path: Path) -> None:
    tree(tmp_path)
    newest = tmp_path / "src/util.py"
    import os
    import time

    os.utime(newest, (time.time() + 10, time.time() + 10))
    result = await call(GLOB, context(tmp_path), pattern="src/*.py")
    first_line = result.content.split("\n")[0]
    assert "util.py" in first_line


async def test_glob_with_no_matches_explains_the_pattern_syntax(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GLOB, context(tmp_path), pattern="**/*.rs")
    assert result.ok
    assert "no files match" in result.content
    assert "**/*.py" in result.content, "hint at the recursive form"


async def test_glob_can_be_scoped_to_a_subdirectory(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GLOB, context(tmp_path), pattern="**/*.py", path="tests")
    assert "test_app.py" in result.content
    assert "app.py" in result.content
    assert "util.py" not in result.content


async def test_glob_refuses_a_path_outside_the_root(tmp_path: Path) -> None:
    result = await call(GLOB, context(tmp_path), pattern="*", path="../..")
    assert not result.ok
    assert "outside the working directory" in result.error


# --------------------------------------------------------------------------- #
# grep
# --------------------------------------------------------------------------- #


async def test_grep_content_mode_returns_matching_lines_with_numbers(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="return None")
    assert result.ok
    assert "app.py" in result.content
    assert ":2:" in result.content, "line numbers on by default"


async def test_grep_files_mode_returns_paths_only(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="handler", mode="files")
    assert "app.py" in result.content
    assert "def handler" not in result.content


async def test_grep_count_mode_returns_per_file_counts(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="return None", mode="count")
    assert result.ok
    assert ":1" in result.content


async def test_grep_honours_a_type_filter(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="handler", type="py", mode="files")
    assert "README.md" not in result.content
    assert "app.py" in result.content


async def test_grep_honours_a_glob_filter(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="handler", glob="*.md", mode="files")
    assert "README.md" in result.content
    assert "app.py" not in result.content


async def test_grep_context_flags_include_surrounding_lines(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="TODO", **{"-C": 2})
    assert "TODO" in result.content
    assert "return None" in result.content, "context should pull in the line above"


async def test_grep_case_insensitivity(tmp_path: Path) -> None:
    tree(tmp_path)
    sensitive = await call(GREP, context(tmp_path), pattern="HANDLER", mode="files")
    assert "no matches" in sensitive.content
    insensitive = await call(
        GREP, context(tmp_path), pattern="HANDLER", mode="files", **{"-i": True}
    )
    assert "app.py" in insensitive.content


async def test_grep_multiline_lets_a_pattern_span_lines(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(
        GREP, context(tmp_path), pattern=r"def handler\(\):.*return", multiline=True, mode="files"
    )
    assert "app.py" in result.content


async def test_grep_with_no_matches_suggests_how_to_widen(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="zzzznotpresent")
    assert result.ok
    assert "no matches" in result.content
    assert "-i" in result.content


async def test_grep_rejects_an_unknown_mode(tmp_path: Path) -> None:
    result = await call(GREP, context(tmp_path), pattern="x", mode="everything")
    assert not result.ok
    assert "mode must be" in result.error


async def test_grep_caps_a_flood_of_matches(tmp_path: Path) -> None:
    write_file(tmp_path, "many.txt", "match\n" * 500)
    result = await call(GREP, context(tmp_path), pattern="match")
    assert result.ok
    assert "more matches not shown" in result.content
    assert "Narrow the search" in result.content


async def test_grep_can_target_a_single_file(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="return", path="src/util.py")
    assert "util.py" in result.content
    assert "app.py" not in result.content


async def test_grep_always_prints_the_filename_even_for_one_file(tmp_path: Path) -> None:
    """Found by a test: ripgrep omits it for a single file, leaving the model with a
    line number and no path to apply it to."""
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="helper", path="src/util.py")
    assert result.content.startswith("src/util.py:")


async def test_grep_prints_paths_relative_to_the_root(tmp_path: Path) -> None:
    """An absolute prefix on every match line is pure token cost."""
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="handler", mode="files")
    assert "src/app.py" in result.content
    assert str(tmp_path) not in result.content


# --------------------------------------------------------------------------- #
# ls
# --------------------------------------------------------------------------- #


async def test_ls_lists_one_level_with_directories_first(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(LS, context(tmp_path), path=".")
    lines = [line for line in result.content.split("\n") if line and not line.startswith("(")]
    assert lines[0].endswith("/"), "directories sort first"
    assert "README.md" in lines
    assert "src/" in lines
    assert "app.py" not in lines, "ls lists one level, it does not recurse"


async def test_ls_honours_ignore_patterns(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(LS, context(tmp_path), path=".", ignore=["*.md", "build"])
    assert "README.md" not in result.content
    assert "build/" not in result.content
    assert "src/" in result.content


async def test_ls_on_a_file_points_at_read(tmp_path: Path) -> None:
    tree(tmp_path)
    result = await call(LS, context(tmp_path), path="README.md")
    assert not result.ok
    assert "Use read" in result.error


async def test_ls_on_a_missing_path_says_so(tmp_path: Path) -> None:
    result = await call(LS, context(tmp_path), path="nowhere")
    assert not result.ok
    assert "does not exist" in result.error


async def test_ls_of_an_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "hollow").mkdir()
    result = await call(LS, context(tmp_path), path="hollow")
    assert result.ok
    assert "empty" in result.content


async def test_ls_rejects_a_non_list_ignore(tmp_path: Path) -> None:
    result = await call(LS, context(tmp_path), path=".", ignore="*.py")
    assert not result.ok
    assert "must be a list" in result.error


# --------------------------------------------------------------------------- #
# Descriptions
# --------------------------------------------------------------------------- #


def test_grep_tells_the_model_not_to_use_bash_grep() -> None:
    """The redirect has to be in the description, not only in bash's refusal."""
    description = GREP.spec().description
    assert "bash" in description
    assert "gitignore" in description


def test_every_search_tool_is_read_only() -> None:
    from ronin.core.types import DangerLevel

    for tool in (GLOB, GREP, LS):
        assert tool.spec().danger_level is DangerLevel.READ_ONLY
        assert not tool.spec().requires_approval


# --------------------------------------------------------------------------- #
# Refusals — the half a happy-path suite hides
# --------------------------------------------------------------------------- #


async def test_grep_refuses_with_install_instructions_when_ripgrep_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """These tools wrap `rg` rather than reimplementing it, so a missing binary is a
    normal environment state — and the refusal has to tell the reader how to fix it,
    not just that something is absent.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    result = await call(GREP, context(tmp_path), pattern="handler")
    assert not result.ok
    assert "ripgrep (rg) is not installed" in result.error
    # The actionable half: at least one real install command.
    assert "apt install ripgrep" in result.error


async def test_grep_reports_ripgreps_own_failure_rather_than_pretending_zero_matches(
    tmp_path: Path,
) -> None:
    """An invalid regex makes `rg` exit 2. Exit 1 means "no matches" and exit 0 means
    "matches"; anything else is a real failure, and reporting it as an empty result
    would tell the model its search succeeded and found nothing.

    Runs the real binary — an unclosed group is genuinely invalid, not a mocked code.
    """
    tree(tmp_path)
    result = await call(GREP, context(tmp_path), pattern="(unclosed")
    assert not result.ok
    assert "ripgrep failed" in result.error


async def test_a_search_timeout_says_how_to_narrow_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout that only says "timed out" leaves the model with no next move.

    The timeout is forced rather than waited for: making a real search take 30s would
    put 30s in the suite, and the branch under test is the message, not the clock.
    """
    tree(tmp_path)

    async def instant_timeout(awaitable: object, *_args: object, **_kwargs: object) -> object:
        # Close the coroutine we are refusing to await, or Python warns that
        # `Process.communicate` was never awaited — a leak in the test, not the code.
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", instant_timeout)
    result = await call(GREP, context(tmp_path), pattern="handler")
    assert not result.ok
    assert "timed out" in result.error
    assert "Narrow it with a path or glob" in result.error
