"""Offline contracts for GitHub/GitLab issue intake into durable missions."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app
from ronin_cli.mission_models import MissionBudget
from ronin_cli.mission_sources import (
    ImportedIssue,
    IssueImportError,
    MissionIssueImporter,
    fetch_github_issue,
    fetch_gitlab_issue,
    parse_github_reference,
    parse_gitlab_reference,
)
from ronin_cli.mission_store import MissionStore


def test_issue_references_are_strict_and_reject_unsafe_shapes() -> None:
    assert parse_github_reference("openai/ronin#42") == ("openai/ronin", 42)
    assert parse_gitlab_reference("platform/agents/ronin#7") == ("platform/agents/ronin", 7)
    for reference in ("ronin#1", "owner/repo#0", "owner/../repo#1", "-owner/repo#1"):
        with pytest.raises(IssueImportError):
            parse_github_reference(reference)
    with pytest.raises(IssueImportError):
        parse_gitlab_reference("group/project#0")


def test_github_import_persists_typed_spec_source_context_and_budget(tmp_path) -> None:
    fetched: list[str] = []

    def github(reference: str) -> ImportedIssue:
        fetched.append(reference)
        return ImportedIssue(
            source="github",
            reference="openai/ronin#42",
            repository_ref="openai/ronin",
            title="Add provider health checks",
            body="Show health evidence before routing.",
            labels=("providers", "reliability"),
            url="https://github.com/openai/ronin/issues/42",
        )

    mission = MissionIssueImporter(tmp_path, github_fetcher=github).import_issue(
        "github", "openai/ronin#42", budget=MissionBudget(max_tokens=123),
    )

    assert fetched == ["openai/ronin#42"]
    assert mission.spec.source == "github"
    assert mission.spec.source_id == "openai/ronin#42"
    assert mission.spec.repository_ref == "openai/ronin"
    assert mission.spec.risk_tags == ["providers", "reliability"]
    assert mission.budget.max_tokens == 123
    assert mission.artifacts.context_packs[0].sources[0].reference.endswith("/42")
    assert mission.event_count == 2
    assert MissionStore(tmp_path).events(mission.id)[-1].actor == "operator"
    assert MissionStore(tmp_path).verify_audit(mission.id).valid


def test_github_fetch_uses_authenticated_helper_and_rejects_pull_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ronin_cli.gh_helper as gh

    monkeypatch.setattr(gh, "gh_available", lambda: True)
    monkeypatch.setattr(gh, "issue_details", lambda *args, **kwargs: {
        "number": 42,
        "title": "Import a durable issue",
        "body": "Keep remote provenance.",
        "labels": [{"name": "agents"}],
        "html_url": "https://github.com/openai/ronin/issues/42",
    })
    issue = fetch_github_issue("openai/ronin#42")
    assert issue.repository_ref == "openai/ronin"
    assert issue.labels == ("agents",)

    monkeypatch.setattr(gh, "issue_details", lambda *args, **kwargs: {"pull_request": {}})
    with pytest.raises(IssueImportError, match="pull request"):
        fetch_github_issue("openai/ronin#42")


def test_gitlab_fetch_uses_https_token_header_and_empty_body_fallback() -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "iid": 9,
                "title": "Improve worker lease recovery",
                "description": "",
                "labels": ["workers", "reliability"],
                "web_url": "https://gitlab.com/platform/ronin/-/issues/9",
            }).encode("utf-8")

    def opener(request, *, timeout: int):
        captured["url"] = request.full_url
        captured["token"] = request.get_header("Private-token")
        captured["timeout"] = timeout
        return Response()

    issue = fetch_gitlab_issue(
        "platform/ronin#9", token="do-not-print", opener=opener,
    )

    assert issue.source == "gitlab"
    assert issue.reference == "platform/ronin#9"
    assert issue.body == "No issue body was provided by the source."
    assert issue.labels == ("workers", "reliability")
    assert captured == {
        "url": "https://gitlab.com/api/v4/projects/platform%2Fronin/issues/9",
        "token": "do-not-print",
        "timeout": 20,
    }


def test_mission_import_cli_uses_the_typed_source_adapter(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ronin_cli.mission_sources as sources

    def github(reference: str, *, root=".") -> ImportedIssue:
        assert reference == "octo/ronin#8"
        return ImportedIssue(
            source="github",
            reference=reference,
            repository_ref="octo/ronin",
            title="Import through CLI",
            body="Keep the source attributable.",
            labels=("integration",),
            url="https://github.com/octo/ronin/issues/8",
        )

    monkeypatch.setattr(sources, "fetch_github_issue", github)
    result = CliRunner().invoke(
        app,
        ["util", "mission", "import", "github", "octo/ronin#8", "--root", str(tmp_path), "--max-repairs", "2"],
    )

    assert result.exit_code == 0, result.stdout
    mission = MissionStore(tmp_path).list()[0]
    assert mission.spec.title == "Import through CLI"
    assert mission.budget.max_repair_attempts == 2
