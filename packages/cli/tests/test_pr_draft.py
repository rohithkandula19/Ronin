"""Tests for pr_draft — PR prompt building + output parsing (no network)."""
from __future__ import annotations

from ronin_cli.pr_draft import fallback_title, parse_pr, pr_prompt


def test_pr_prompt_includes_commits_and_diffstat() -> None:
    p = pr_prompt(
        "feat/cache",
        "main",
        " src/cache.py | 12 ++++++\n 1 file changed",
        ["add LRU cache", "wire cache into reads"],
    )
    # the branch + base are named, and the real commits + diffstat are grounded in
    assert "feat/cache" in p and "main" in p
    assert "add LRU cache" in p and "wire cache into reads" in p
    assert "src/cache.py" in p and "1 file changed" in p


def test_pr_prompt_handles_no_commits() -> None:
    p = pr_prompt("wip", "main", "", [])
    assert "no commits" in p.lower()


def test_parse_pr_splits_heading_title_and_body() -> None:
    title, body = parse_pr("# Add caching\n\nSpeeds up reads.\n\n## Changes\n- LRU cache")
    assert title == "Add caching"
    assert body.startswith("Speeds up reads.")
    assert "## Changes" in body


def test_parse_pr_title_only() -> None:
    title, body = parse_pr("Add caching")
    assert title == "Add caching"
    assert body == ""


def test_parse_pr_strips_leading_blank_lines_and_backticks() -> None:
    title, body = parse_pr("\n\n`Add caching`\n\nbody here")
    assert title == "Add caching"
    assert body == "body here"


def test_parse_pr_empty_input() -> None:
    assert parse_pr("") == ("", "")
    assert parse_pr("   \n  ") == ("", "")


def test_fallback_title_single_commit_strips_prefix() -> None:
    assert fallback_title(["feat(cli): add ronin pr command"]) == "add ronin pr command"


def test_fallback_title_single_plain_commit() -> None:
    assert fallback_title(["Add caching layer"]) == "Add caching layer"


def test_fallback_title_many_commits_summarizes() -> None:
    assert fallback_title(["a", "b", "c"]) == "3 changes from this branch"


def test_fallback_title_empty() -> None:
    assert fallback_title([]) == "Update branch"
