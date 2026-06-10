"""Tests for the changelog generator — parse, group, render."""
from __future__ import annotations

from ronin_cli.changelog import group_commits, parse_commit, render_changelog


def test_parse_conventional() -> None:
    c = parse_commit("feat(router): add self-tuning")
    assert c.type == "feat" and c.scope == "router"
    assert c.subject == "add self-tuning" and not c.breaking


def test_parse_breaking() -> None:
    c = parse_commit("refactor!: rename modules")
    assert c.type == "refactor" and c.breaking


def test_parse_non_conventional_is_other() -> None:
    c = parse_commit("just did some stuff")
    assert c.type == "other" and c.subject == "just did some stuff"


def test_unknown_type_is_other() -> None:
    assert parse_commit("wip: poking around").type == "other"


def test_group_commits() -> None:
    groups = group_commits(["feat: a", "fix: b", "feat: c", ""])
    assert len(groups["feat"]) == 2 and len(groups["fix"]) == 1


def test_render_sections_and_order() -> None:
    md = render_changelog(["feat: new thing", "fix: a bug", "docs: readme"],
                          version="1.2.0", date="2026-06-02")
    assert md.startswith("## [1.2.0] — 2026-06-02")
    assert "### Added" in md and "### Fixed" in md and "### Docs" in md
    assert md.index("### Added") < md.index("### Fixed")   # feat before fix
    assert "new thing" in md


def test_render_breaking_first() -> None:
    md = render_changelog(["feat!: big change", "fix: small"])
    assert "### ⚠ BREAKING CHANGES" in md
    assert md.index("BREAKING") < md.index("### Fixed")


def test_render_empty() -> None:
    assert "_No notable changes._" in render_changelog([])


def test_scope_formatting() -> None:
    md = render_changelog(["feat(api): add endpoint"])
    assert "**api:** add endpoint" in md
