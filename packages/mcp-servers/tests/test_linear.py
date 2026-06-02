from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from ronin_mcp_servers import LinearReadOnlyTools, linear_tools


def _gql_response(data: dict, status: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = {"data": data}
    response.raise_for_status = MagicMock()
    return response


def _gql_error_response(errors: list[dict]) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {"errors": errors}
    response.raise_for_status = MagicMock()
    return response


def test_list_teams() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _gql_response({
        "teams": {"nodes": [{"id": "t1", "key": "ENG", "name": "Engineering", "description": "core"}]}
    })
    linear = LinearReadOnlyTools(api_key="lin_api_x", http=fake)

    teams = linear.list_teams()
    assert teams == [{"id": "t1", "key": "ENG", "name": "Engineering", "description": "core"}]
    args, kwargs = fake.post.call_args
    assert args[0].endswith("/graphql")
    assert kwargs["json"]["variables"]["first"] == 25


def test_list_issues_with_filters() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _gql_response({
        "issues": {"nodes": [{"id": "i1", "identifier": "ENG-1", "title": "fix bug"}]}
    })
    linear = LinearReadOnlyTools(api_key="lin_api_x", http=fake)
    issues = linear.list_issues(team_id="t1", state="In Progress", limit=5)

    assert issues[0]["identifier"] == "ENG-1"
    sent = fake.post.call_args.kwargs["json"]
    assert sent["variables"]["first"] == 5
    assert sent["variables"]["filter"]["team"]["id"]["eq"] == "t1"
    assert sent["variables"]["filter"]["state"]["name"]["eq"] == "In Progress"


def test_get_issue_returns_payload() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _gql_response({
        "issue": {"id": "i1", "identifier": "ENG-1", "title": "x"}
    })
    linear = LinearReadOnlyTools(api_key="lin_api_x", http=fake)
    issue = linear.get_issue("ENG-1")
    assert issue["identifier"] == "ENG-1"


def test_get_issue_raises_on_missing() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _gql_response({"issue": None})
    linear = LinearReadOnlyTools(api_key="lin_api_x", http=fake)
    with pytest.raises(LookupError):
        linear.get_issue("ENG-9999")


def test_get_issue_requires_identifier() -> None:
    linear = LinearReadOnlyTools(api_key="lin_api_x", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError, match="identifier"):
        linear.get_issue("")


def test_graphql_errors_propagate() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _gql_error_response([{"message": "auth failed"}])
    linear = LinearReadOnlyTools(api_key="lin_api_x", http=fake)
    with pytest.raises(RuntimeError, match="GraphQL errors"):
        linear.list_teams()


def test_limit_clamped() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _gql_response({"projects": {"nodes": []}})
    linear = LinearReadOnlyTools(api_key="lin_api_x", http=fake, max_limit=10)
    linear.list_projects(limit=10_000)
    assert fake.post.call_args.kwargs["json"]["variables"]["first"] == 10


def test_factory_handlers() -> None:
    handlers = linear_tools(api_key="lin_api_x")
    assert set(handlers) == {"linear_list_teams", "linear_list_projects", "linear_list_issues", "linear_get_issue"}
    assert all(callable(h) for h in handlers.values())
