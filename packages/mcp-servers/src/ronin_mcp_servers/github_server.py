"""GitHub read-only MCP server reference.

Wraps GitHub's REST v3 API. Read-only by design — no write paths exist here.
Auth: ``GITHUB_TOKEN`` env var (a fine-grained personal access token scoped to
read-only resources is recommended).

Tools shipped:
- ``list_repos(owner)`` — list repos for a user or org
- ``list_issues(owner, repo, state, labels, limit)``
- ``get_issue(owner, repo, number)``
- ``list_pulls(owner, repo, state, limit)``
- ``list_commits(owner, repo, since, until, limit)``
- ``search_code(query, limit)`` — code search
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

GITHUB_BASE = "https://api.github.com"


class GitHubReadOnlyTools(BaseModel):
    """GitHub REST API wrapper (read-only)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    token: str = Field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    http: Any = None
    base_url: str = GITHUB_BASE
    max_limit: int = 100

    def _client(self) -> httpx.Client:
        if self.http is not None:
            return self.http
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN not set; pass token= or set the env var")
        return httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._client().get(path, params=params)
        response.raise_for_status()
        return response.json()

    def _clamp(self, limit: int) -> int:
        return max(1, min(self.max_limit, int(limit)))

    def list_repos(self, owner: str, limit: int = 30) -> list[dict[str, Any]]:
        if not owner:
            raise ValueError("owner is required")
        # Works for both users and orgs — try org first, fall back to user
        client = self._client()
        org_resp = client.get(f"/orgs/{owner}/repos", params={"per_page": self._clamp(limit)})
        if org_resp.status_code == 200:
            return org_resp.json()
        return self._get(f"/users/{owner}/repos", params={"per_page": self._clamp(limit)})

    def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        if not owner or not repo:
            raise ValueError("owner and repo are required")
        if state not in ("open", "closed", "all"):
            raise ValueError("state must be 'open', 'closed', or 'all'")
        params: dict[str, Any] = {"state": state, "per_page": self._clamp(limit)}
        if labels:
            params["labels"] = ",".join(labels)
        issues = self._get(f"/repos/{owner}/{repo}/issues", params=params)
        # GitHub returns PRs in /issues too — filter them out
        return [i for i in issues if "pull_request" not in i]

    def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        if not owner or not repo or not number:
            raise ValueError("owner, repo, and number are required")
        return self._get(f"/repos/{owner}/{repo}/issues/{int(number)}")

    def list_pulls(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        if not owner or not repo:
            raise ValueError("owner and repo are required")
        if state not in ("open", "closed", "all"):
            raise ValueError("state must be 'open', 'closed', or 'all'")
        return self._get(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": self._clamp(limit)},
        )

    def list_commits(
        self,
        owner: str,
        repo: str,
        since: str | None = None,
        until: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        if not owner or not repo:
            raise ValueError("owner and repo are required")
        params: dict[str, Any] = {"per_page": self._clamp(limit)}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return self._get(f"/repos/{owner}/{repo}/commits", params=params)

    def search_code(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not query:
            raise ValueError("query is required")
        data = self._get("/search/code", params={"q": query, "per_page": self._clamp(limit)})
        return data.get("items", [])


def github_tools(token: str | None = None) -> dict[str, Any]:
    backend = GitHubReadOnlyTools(token=token) if token else GitHubReadOnlyTools()
    return {
        "github_list_repos": backend.list_repos,
        "github_list_issues": backend.list_issues,
        "github_get_issue": backend.get_issue,
        "github_list_pulls": backend.list_pulls,
        "github_list_commits": backend.list_commits,
        "github_search_code": backend.search_code,
    }
