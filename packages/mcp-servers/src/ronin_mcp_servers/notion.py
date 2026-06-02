"""Notion read-only MCP server reference.

Wraps Notion's REST API behind read-only tools. Auth: an internal-integration
secret (``NOTION_TOKEN`` env var or ``token`` parameter). Make sure the
integration is shared only with the pages/databases you want exposed.

Tools shipped:
- ``search(query, filter, page_size)``
- ``retrieve_page(page_id)``
- ``retrieve_database(database_id)``
- ``query_database(database_id, filter, sorts, page_size)``
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionReadOnlyTools(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    token: str = Field(default_factory=lambda: os.environ.get("NOTION_TOKEN", ""))
    http: Any = None
    base_url: str = NOTION_BASE
    max_page_size: int = 100

    def _client(self) -> httpx.Client:
        if self.http is not None:
            return self.http
        if not self.token:
            raise RuntimeError("NOTION_TOKEN not set; pass token= or set the env var")
        return httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client().request(method, path, json=json)
        response.raise_for_status()
        return response.json()

    def _clamp_page_size(self, page_size: int) -> int:
        return max(1, min(self.max_page_size, int(page_size)))

    def search(
        self,
        query: str = "",
        filter: dict[str, Any] | None = None,
        page_size: int = 25,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"query": query, "page_size": self._clamp_page_size(page_size)}
        if filter:
            body["filter"] = filter
        return self._request("POST", "/search", body).get("results", [])

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        if not page_id:
            raise ValueError("page_id is required")
        return self._request("GET", f"/pages/{page_id}")

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        if not database_id:
            raise ValueError("database_id is required")
        return self._request("GET", f"/databases/{database_id}")

    def query_database(
        self,
        database_id: str,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        page_size: int = 25,
    ) -> list[dict[str, Any]]:
        if not database_id:
            raise ValueError("database_id is required")
        body: dict[str, Any] = {"page_size": self._clamp_page_size(page_size)}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        return self._request("POST", f"/databases/{database_id}/query", body).get("results", [])


def notion_tools(token: str | None = None) -> dict[str, Any]:
    backend = NotionReadOnlyTools(token=token) if token else NotionReadOnlyTools()
    return {
        "notion_search": backend.search,
        "notion_retrieve_page": backend.retrieve_page,
        "notion_retrieve_database": backend.retrieve_database,
        "notion_query_database": backend.query_database,
    }
