"""Linear read-only MCP server reference.

GraphQL-based wrapper around Linear's API. Read-only by design — write ops
(creating / updating issues) intentionally omitted; add them behind an
``ApprovalGate`` from ``ronin_hardening``.

Auth: ``LINEAR_API_KEY`` env var, or pass ``api_key`` directly. Use a personal
API key with read-only scope from https://linear.app/settings/api.

Tools shipped:
- ``list_teams()``
- ``list_projects(limit)``
- ``list_issues(team_id, state, limit)``
- ``get_issue(identifier)`` where identifier is e.g. ``"ENG-123"``
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


_TEAMS_QUERY = """
query Teams($first: Int!) {
  teams(first: $first) {
    nodes { id key name description }
  }
}
"""

_PROJECTS_QUERY = """
query Projects($first: Int!) {
  projects(first: $first) {
    nodes { id name state startDate targetDate }
  }
}
"""

_ISSUES_QUERY = """
query Issues($first: Int!, $filter: IssueFilter) {
  issues(first: $first, filter: $filter) {
    nodes { id identifier title state { name } priority assignee { name } updatedAt }
  }
}
"""

_ISSUE_QUERY = """
query Issue($id: String!) {
  issue(id: $id) {
    id identifier title description state { name } priority
    assignee { name email } team { key name }
    createdAt updatedAt url
  }
}
"""


class LinearReadOnlyTools(BaseModel):
    """Linear GraphQL wrapper. Pass a custom ``http`` client for testing."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    api_key: str = Field(default_factory=lambda: os.environ.get("LINEAR_API_KEY", ""))
    http: Any = None
    url: str = LINEAR_GRAPHQL_URL
    max_limit: int = 100

    def _client(self) -> httpx.Client:
        if self.http is not None:
            return self.http
        if not self.api_key:
            raise RuntimeError("LINEAR_API_KEY not set; pass api_key= or set the env var")
        return httpx.Client(timeout=30, headers={"Authorization": self.api_key})

    def _query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client().post(self.url, json={"query": query, "variables": variables or {}})
        response.raise_for_status()
        body = response.json()
        if "errors" in body:
            raise RuntimeError(f"Linear GraphQL errors: {body['errors']}")
        return body["data"]

    def _clamp(self, limit: int) -> int:
        return max(1, min(self.max_limit, int(limit)))

    def list_teams(self, limit: int = 25) -> list[dict[str, Any]]:
        data = self._query(_TEAMS_QUERY, {"first": self._clamp(limit)})
        return data["teams"]["nodes"]

    def list_projects(self, limit: int = 25) -> list[dict[str, Any]]:
        data = self._query(_PROJECTS_QUERY, {"first": self._clamp(limit)})
        return data["projects"]["nodes"]

    def list_issues(
        self,
        team_id: str | None = None,
        state: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        filter_arg: dict[str, Any] = {}
        if team_id:
            filter_arg["team"] = {"id": {"eq": team_id}}
        if state:
            filter_arg["state"] = {"name": {"eq": state}}
        data = self._query(
            _ISSUES_QUERY,
            {"first": self._clamp(limit), "filter": filter_arg or None},
        )
        return data["issues"]["nodes"]

    def get_issue(self, identifier: str) -> dict[str, Any]:
        if not identifier:
            raise ValueError("identifier required (e.g. 'ENG-123' or a Linear UUID)")
        data = self._query(_ISSUE_QUERY, {"id": identifier})
        issue = data.get("issue")
        if issue is None:
            raise LookupError(f"issue {identifier!r} not found")
        return issue


def linear_tools(api_key: str | None = None) -> dict[str, Any]:
    """Build a name -> handler dict ready to register with any MCP / agent runtime."""
    backend = LinearReadOnlyTools(api_key=api_key) if api_key else LinearReadOnlyTools()
    return {
        "linear_list_teams": backend.list_teams,
        "linear_list_projects": backend.list_projects,
        "linear_list_issues": backend.list_issues,
        "linear_get_issue": backend.get_issue,
    }
