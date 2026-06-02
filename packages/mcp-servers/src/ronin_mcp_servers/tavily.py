"""Tavily web-search MCP server reference.

Tavily (https://tavily.com) is a search API tuned for LLM consumption — it returns
clean snippets ready to feed an agent, not raw HTML. Free tier gives 1000 searches/mo.

Auth: ``TAVILY_API_KEY`` env var or pass ``api_key`` directly.

Tools shipped:
- ``search(query, max_results, include_answer)`` — web search with optional AI-summarized answer
- ``extract(urls)`` — pull clean text content from one or more URLs
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

TAVILY_BASE = "https://api.tavily.com"


class TavilyTools(BaseModel):
    """Tavily REST wrapper. Read-only by design — there are no write endpoints."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    api_key: str = Field(default_factory=lambda: os.environ.get("TAVILY_API_KEY", ""))
    http: Any = None
    base_url: str = TAVILY_BASE
    max_results_cap: int = 20

    def _client(self) -> httpx.Client:
        if self.http is not None:
            return self.http
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY not set; pass api_key= or set the env var")
        return httpx.Client(base_url=self.base_url, timeout=30)

    def search(
        self,
        query: str,
        max_results: int = 5,
        include_answer: bool = True,
        search_depth: str = "basic",  # "basic" | "advanced"
    ) -> dict[str, Any]:
        """Run a web search. Returns ``{answer, results: [{title, url, content, score}]}``."""
        if not query:
            raise ValueError("query is required")
        body = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max(1, min(self.max_results_cap, int(max_results))),
            "include_answer": include_answer,
            "search_depth": search_depth if search_depth in ("basic", "advanced") else "basic",
        }
        response = self._client().post("/search", json=body)
        response.raise_for_status()
        return response.json()

    def extract(self, urls: list[str]) -> list[dict[str, Any]]:
        """Pull clean text from up to 20 URLs. Returns list of ``{url, raw_content, content}``."""
        if not urls:
            raise ValueError("urls is required and must be non-empty")
        if len(urls) > 20:
            raise ValueError("max 20 urls per extract call")
        body = {"api_key": self.api_key, "urls": urls}
        response = self._client().post("/extract", json=body)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])


def tavily_tools(api_key: str | None = None) -> dict[str, Any]:
    """Build a name -> handler dict for any MCP / agent runtime."""
    backend = TavilyTools(api_key=api_key) if api_key else TavilyTools()
    return {
        "web_search": backend.search,
        "web_extract": backend.extract,
    }
