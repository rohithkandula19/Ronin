"""Tests for the changelog generator — parse, group, render, collect."""
from __future__ import annotations

import subprocess

from ronin_cli.changelog import (
    collect_commits,
    group_commits,
    parse_commit,
    render_changelog,
)


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


# --- collect_commits (impure: drives real git on a temp repo, no network) ---

def _git(root, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def _repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.test")
    _git(path, "config", "user.name", "Test")
    return path


def test_collect_outside_repo_is_empty(tmp_path) -> None:
    assert collect_commits(tmp_path) == []


def test_collect_parses_and_orders(tmp_path) -> None:
    _repo(tmp_path)
    for subj in ("feat(cli): add thing", "fix: a bug", "noise here"):
        (tmp_path / "f.txt").write_text(subj)
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", subj)
    commits = collect_commits(tmp_path)
    # newest-first ordering
    assert [c["subject"] for c in commits] == ["noise here", "a bug", "add thing"]
    assert len(commits[0]["hash"]) == 40
    feat = next(c for c in commits if c["subject"] == "add thing")
    assert feat["type"] == "feat" and feat["scope"] == "cli"
    assert next(c for c in commits if c["subject"] == "noise here")["type"] == "other"


def test_collect_since_ref_excludes_older(tmp_path) -> None:
    _repo(tmp_path)
    (tmp_path / "f.txt").write_text("a")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feat: before tag")
    _git(tmp_path, "tag", "v1")
    (tmp_path / "f.txt").write_text("b")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "fix: after tag")
    subjects = [c["subject"] for c in collect_commits(tmp_path, since="v1")]
    assert subjects == ["after tag"]
    # since=None auto-detects the latest tag → same result here
    assert [c["subject"] for c in collect_commits(tmp_path)] == ["after tag"]
