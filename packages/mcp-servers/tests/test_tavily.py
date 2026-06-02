from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from ronin_mcp_servers import TavilyTools, tavily_tools


def _resp(payload: dict) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def test_search() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _resp({
        "answer": "ReAct is a reasoning + acting pattern.",
        "results": [{"title": "ReAct paper", "url": "https://arxiv.org/x", "content": "...", "score": 0.95}],
    })
    tavily = TavilyTools(api_key="tvly-test", http=fake)

    out = tavily.search("ReAct pattern", max_results=3)
    assert "answer" in out
    assert out["results"][0]["title"] == "ReAct paper"

    path = fake.post.call_args.args[0]
    body = fake.post.call_args.kwargs["json"]
    assert path == "/search"
    assert body["api_key"] == "tvly-test"
    assert body["query"] == "ReAct pattern"
    assert body["max_results"] == 3
    assert body["include_answer"] is True


def test_search_clamps_max_results() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _resp({"answer": "", "results": []})
    tavily = TavilyTools(api_key="tvly-test", http=fake, max_results_cap=10)
    tavily.search("x", max_results=9999)
    assert fake.post.call_args.kwargs["json"]["max_results"] == 10


def test_search_validates_query() -> None:
    tavily = TavilyTools(api_key="tvly-test", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError, match="query"):
        tavily.search("")


def test_search_rejects_unknown_depth() -> None:
    """An unknown ``search_depth`` should fall back to 'basic', not pass through."""
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _resp({"answer": "", "results": []})
    tavily = TavilyTools(api_key="tvly-test", http=fake)
    tavily.search("x", search_depth="hyperdrive")
    assert fake.post.call_args.kwargs["json"]["search_depth"] == "basic"


def test_extract() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _resp({
        "results": [{"url": "https://example.com", "content": "clean text", "raw_content": "..."}],
    })
    tavily = TavilyTools(api_key="tvly-test", http=fake)
    results = tavily.extract(["https://example.com"])
    assert results[0]["content"] == "clean text"


def test_extract_validates_urls() -> None:
    tavily = TavilyTools(api_key="tvly-test", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError, match="urls is required"):
        tavily.extract([])
    with pytest.raises(ValueError, match="max 20"):
        tavily.extract([f"https://e{i}.com" for i in range(21)])


def test_factory_handlers() -> None:
    handlers = tavily_tools(api_key="tvly-test")
    assert set(handlers) == {"web_search", "web_extract"}
    assert all(callable(h) for h in handlers.values())
