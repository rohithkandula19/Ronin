"""Typed, source-attributed GitHub and GitLab issue intake for missions.

The adapters import issue metadata into a ``MissionSpec`` and an attributable
``ContextPack``. They do not run agents, create branches, or post comments.
GitHub reuses the authenticated ``gh`` CLI. GitLab reads a token only from the
process environment and never stores or reports it.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from .mission_models import (
    ContextPack,
    ContextSource,
    MissionArtifacts,
    MissionBudget,
    MissionRecord,
    MissionSpec,
)
from .mission_store import MissionStore


_REF_PART = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_GITHUB_REF = re.compile(rf"^(?P<repository>{_REF_PART}/{_REF_PART})#(?P<number>[1-9][0-9]*)$")
_GITLAB_REF = re.compile(rf"^(?P<project>{_REF_PART}(?:/{_REF_PART})+)#(?P<number>[1-9][0-9]*)$")
_LABEL_LIMIT = 50
_BODY_LIMIT = 50_000


class IssueImportError(ValueError):
    """A safe, operator-actionable issue import failure."""


@dataclass(frozen=True)
class ImportedIssue:
    source: Literal["github", "gitlab"]
    reference: str
    repository_ref: str
    title: str
    body: str
    labels: tuple[str, ...]
    url: str


def parse_github_reference(reference: str) -> tuple[str, int]:
    match = _GITHUB_REF.fullmatch(reference.strip())
    if not match:
        raise IssueImportError("GitHub reference must use owner/repository#number")
    return match.group("repository"), int(match.group("number"))


def parse_gitlab_reference(reference: str) -> tuple[str, int]:
    match = _GITLAB_REF.fullmatch(reference.strip())
    if not match:
        raise IssueImportError("GitLab reference must use group/project#number")
    return match.group("project"), int(match.group("number"))


def fetch_github_issue(reference: str, *, root: Path | str = ".") -> ImportedIssue:
    """Read one GitHub issue through ``gh`` without exposing credentials or stderr."""
    from .gh_helper import gh_available, issue_details

    repository, number = parse_github_reference(reference)
    if not gh_available():
        raise IssueImportError("GitHub issue import needs the gh CLI; install it and run gh auth login")
    payload = issue_details(repository, number, root=root)
    if payload is None:
        raise IssueImportError("GitHub issue could not be read; check gh authentication and repository access")
    if "pull_request" in payload:
        raise IssueImportError("the reference is a pull request; import an issue instead")
    return _github_payload(repository, payload)


def fetch_gitlab_issue(
    reference: str,
    *,
    base_url: str = "https://gitlab.com",
    token: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> ImportedIssue:
    """Read one GitLab issue using an explicit environment-provided token."""
    project, number = parse_gitlab_reference(reference)
    token = token or os.environ.get("GITLAB_TOKEN") or os.environ.get("GITLAB_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise IssueImportError("GitLab issue import needs GITLAB_TOKEN or GITLAB_PERSONAL_ACCESS_TOKEN")
    clean_base = base_url.rstrip("/")
    parsed = urllib.parse.urlparse(clean_base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise IssueImportError("GitLab URL must use a trusted https endpoint")
    encoded_project = urllib.parse.quote(project, safe="")
    url = f"{clean_base}/api/v4/projects/{encoded_project}/issues/{number}"
    request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"})
    try:
        with opener(request, timeout=20) as response:  # noqa: S310 - validated HTTPS endpoint
            raw = response.read().decode("utf-8")
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, UnicodeDecodeError):
        raise IssueImportError("GitLab issue could not be read; check token, URL, and repository access") from None
    try:
        payload = json.loads(raw)
    except ValueError:
        raise IssueImportError("GitLab returned an invalid issue response") from None
    if not isinstance(payload, dict):
        raise IssueImportError("GitLab returned an invalid issue response")
    return _gitlab_payload(project, payload)


class MissionIssueImporter:
    """Create durable missions from a supported remote issue source."""

    def __init__(
        self,
        root: Path | str,
        *,
        github_fetcher: Callable[[str], ImportedIssue] | None = None,
        gitlab_fetcher: Callable[[str], ImportedIssue] | None = None,
        gitlab_url: str = "https://gitlab.com",
    ) -> None:
        self.root = Path(root).resolve()
        self.store = MissionStore(self.root)
        self._github_fetcher = github_fetcher or (lambda reference: fetch_github_issue(reference, root=self.root))
        self._gitlab_fetcher = gitlab_fetcher or (
            lambda reference: fetch_gitlab_issue(reference, base_url=gitlab_url)
        )

    def import_issue(
        self,
        source: str,
        reference: str,
        *,
        budget: MissionBudget | None = None,
    ) -> MissionRecord:
        normalized = source.strip().lower()
        if normalized == "github":
            issue = self._github_fetcher(reference)
        elif normalized == "gitlab":
            issue = self._gitlab_fetcher(reference)
        else:
            raise IssueImportError("mission import source must be github or gitlab")
        spec = MissionSpec(
            source=issue.source,
            source_id=issue.reference,
            title=issue.title,
            issue_text=issue.body,
            repository_ref=issue.repository_ref,
            risk_tags=list(issue.labels),
        )
        mission = self.store.create(spec, budget=budget)
        context = ContextPack(
            role="issue-import",
            summary=f"Imported {issue.source} issue {issue.reference} from {issue.repository_ref}.",
            sources=[ContextSource(kind="issue", reference=issue.url or issue.reference, relevance=1.0)],
        )
        return self.store.save_artifacts(
            mission.id,
            MissionArtifacts(context_packs=[context]),
            actor="operator",
        )


def _github_payload(repository: str, payload: Mapping[str, Any]) -> ImportedIssue:
    number = payload.get("number")
    title = _required_text(payload.get("title"), "GitHub issue has no usable title")
    if not isinstance(number, int) or number < 1:
        raise IssueImportError("GitHub returned an invalid issue number")
    labels = _labels(payload.get("labels"))
    return ImportedIssue(
        source="github",
        reference=f"{repository}#{number}",
        repository_ref=repository,
        title=title,
        body=_body(payload.get("body")),
        labels=labels,
        url=_url(payload.get("html_url")),
    )


def _gitlab_payload(project: str, payload: Mapping[str, Any]) -> ImportedIssue:
    number = payload.get("iid")
    title = _required_text(payload.get("title"), "GitLab issue has no usable title")
    if not isinstance(number, int) or number < 1:
        raise IssueImportError("GitLab returned an invalid issue number")
    return ImportedIssue(
        source="gitlab",
        reference=f"{project}#{number}",
        repository_ref=project,
        title=title,
        body=_body(payload.get("description")),
        labels=_labels(payload.get("labels")),
        url=_url(payload.get("web_url")),
    )


def _required_text(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IssueImportError(message)
    return value.strip()[:500]


def _body(value: Any) -> str:
    text = value[:_BODY_LIMIT] if isinstance(value, str) else ""
    return text if text.strip() else "No issue body was provided by the source."


def _labels(value: Any) -> tuple[str, ...]:
    labels: list[str] = []
    if not isinstance(value, list):
        return ()
    for item in value:
        label = item.get("name") if isinstance(item, dict) else item
        if not isinstance(label, str):
            continue
        normalized = label.strip()[:100]
        if normalized and normalized not in labels:
            labels.append(normalized)
        if len(labels) >= _LABEL_LIMIT:
            break
    return tuple(labels)


def _url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urllib.parse.urlparse(value.strip())
    return value.strip()[:1_000] if parsed.scheme == "https" and parsed.netloc else ""
