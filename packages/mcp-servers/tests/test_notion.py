from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from ronin_mcp_servers import NotionReadOnlyTools, notion_tools


def _resp(payload: dict, status: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def test_search() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.request.return_value = _resp({"results": [{"id": "p1", "object": "page"}]})
    notion = NotionReadOnlyTools(token="secret_x", http=fake)

    results = notion.search("agents", page_size=10)
    assert results == [{"id": "p1", "object": "page"}]
    method, path = fake.request.call_args.args
    assert method == "POST"
    assert path == "/search"
    assert fake.request.call_args.kwargs["json"]["query"] == "agents"
    assert fake.request.call_args.kwargs["json"]["page_size"] == 10


def test_search_clamps_page_size() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.request.return_value = _resp({"results": []})
    notion = NotionReadOnlyTools(token="secret_x", http=fake, max_page_size=50)
    notion.search("x", page_size=10_000)
    assert fake.request.call_args.kwargs["json"]["page_size"] == 50


def test_retrieve_page_requires_id() -> None:
    notion = NotionReadOnlyTools(token="secret_x", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError, match="page_id"):
        notion.retrieve_page("")


def test_retrieve_page() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.request.return_value = _resp({"id": "p1", "object": "page", "properties": {}})
    notion = NotionReadOnlyTools(token="secret_x", http=fake)
    page = notion.retrieve_page("p1")
    assert page["id"] == "p1"
    assert fake.request.call_args.args == ("GET", "/pages/p1")


def test_retrieve_database() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.request.return_value = _resp({"id": "db1", "object": "database"})
    notion = NotionReadOnlyTools(token="secret_x", http=fake)
    db = notion.retrieve_database("db1")
    assert db["id"] == "db1"
    assert fake.request.call_args.args == ("GET", "/databases/db1")


def test_query_database_with_filter_and_sorts() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.request.return_value = _resp({"results": [{"id": "row1"}]})
    notion = NotionReadOnlyTools(token="secret_x", http=fake)

    results = notion.query_database(
        "db1",
        filter={"property": "Status", "select": {"equals": "Done"}},
        sorts=[{"property": "Updated", "direction": "descending"}],
        page_size=5,
    )
    assert results == [{"id": "row1"}]

    method, path = fake.request.call_args.args
    assert method == "POST"
    assert path == "/databases/db1/query"
    body = fake.request.call_args.kwargs["json"]
    assert body["filter"]["property"] == "Status"
    assert body["sorts"][0]["direction"] == "descending"
    assert body["page_size"] == 5


def test_query_database_requires_id() -> None:
    notion = NotionReadOnlyTools(token="secret_x", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError, match="database_id"):
        notion.query_database("")


def test_factory_handlers() -> None:
    handlers = notion_tools(token="secret_x")
    assert set(handlers) == {
        "notion_search",
        "notion_retrieve_page",
        "notion_retrieve_database",
        "notion_query_database",
    }
