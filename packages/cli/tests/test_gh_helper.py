"""Tests for the GitHub helpers — pure JSON parsing + soft-fail wrappers."""
from __future__ import annotations

from ronin_cli import gh_helper


def test_parse_issues_well_formed() -> None:
    raw = (
        '[{"number": 12, "title": "Crash on empty input", '
        '"body": "stack trace here", "labels": [{"name": "bug"}, {"name": "P1"}]},'
        '{"number": 8, "title": "Add dark mode", "body": "", "labels": []}]'
    )
    issues = gh_helper.parse_issues(raw)
    assert len(issues) == 2
    assert issues[0]["number"] == 12 and issues[0]["title"] == "Crash on empty input"
    assert issues[0]["labels"] == ["bug", "P1"]
    assert issues[1]["labels"] == []


def test_parse_issues_truncates_body() -> None:
    raw = '[{"number": 1, "title": "t", "body": "' + "x" * 5000 + '"}]'
    issues = gh_helper.parse_issues(raw)
    assert len(issues[0]["body"]) == 2000


def test_parse_issues_bad_json_is_empty() -> None:
    assert gh_helper.parse_issues("not json") == []
    assert gh_helper.parse_issues("") == []


def test_pr_diff_soft_fails_without_gh(monkeypatch, tmp_path) -> None:
    # simulate gh missing → wrappers return None/[] not raise
    monkeypatch.setattr(gh_helper.shutil, "which", lambda _: None)
    assert gh_helper.gh_available() is False


def test_pr_diff_returns_none_on_error(monkeypatch, tmp_path) -> None:
    class P:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(gh_helper, "_gh", lambda *a, **k: P())
    assert gh_helper.pr_diff(5, tmp_path) is None


def test_pr_diff_returns_stdout_on_success(monkeypatch, tmp_path) -> None:
    class P:
        returncode = 0
        stdout = "diff --git a/x b/x\n+hi"
    monkeypatch.setattr(gh_helper, "_gh", lambda *a, **k: P())
    assert "diff --git" in gh_helper.pr_diff(5, tmp_path)
