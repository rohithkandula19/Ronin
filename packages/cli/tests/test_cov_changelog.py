"""Edge-case coverage for the changelog generator."""
from __future__ import annotations

import pytest

from ronin_cli.changelog import parse_commit, render_changelog


@pytest.mark.parametrize("subject,typ", [
    ("feat: x", "feat"),
    ("fix: y", "fix"),
    ("perf: z", "perf"),
    ("refactor: r", "refactor"),
    ("docs: d", "docs"),
    ("test: t", "test"),
    ("chore: c", "chore"),
    ("wip: w", "other"),
    ("random text", "other"),
])
def test_type_mapping(subject, typ) -> None:
    assert parse_commit(subject).type == typ


def test_scope_and_breaking_combined() -> None:
    c = parse_commit("feat(api)!: drop v1")
    assert c.scope == "api" and c.breaking and c.type == "feat"


def test_render_groups_perf_and_chore() -> None:
    md = render_changelog(["perf: faster", "chore: bump deps"])
    assert "### Performance" in md and "### Chores" in md


def test_render_other_section() -> None:
    md = render_changelog(["just did stuff", "more stuff"])
    assert "### Other" in md and "just did stuff" in md


def test_render_skips_blank_subjects() -> None:
    md = render_changelog(["feat: a", "", "  ", "fix: b"])
    assert md.count("- ") == 2


def test_render_version_no_date() -> None:
    md = render_changelog(["feat: a"], version="2.0.0")
    assert md.startswith("## [2.0.0]") and "—" not in md.splitlines()[0]


def test_breaking_section_lists_all_breaking() -> None:
    md = render_changelog(["feat!: a", "fix!: b", "docs: c"])
    head = md[:md.index("###", 5)] if "###" in md else md
    assert md.count("BREAKING") == 1
    assert md.index("BREAKING") < md.index("### Docs")
