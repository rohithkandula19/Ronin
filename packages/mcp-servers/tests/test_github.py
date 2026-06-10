from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from ronin_mcp_servers import GitHubReadOnlyTools, github_tools


def _resp(payload, status: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    if status >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=response
        )
    return response


def test_list_repos_org_path() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _resp([{"name": "repo-a"}, {"name": "repo-b"}])
    gh = GitHubReadOnlyTools(token="ghp_test", http=fake)

    repos = gh.list_repos("acme")
    assert [r["name"] for r in repos] == ["repo-a", "repo-b"]
    # First call is the org probe
    first_args, first_kwargs = fake.get.call_args_list[0]
    assert first_args[0] == "/orgs/acme/repos"


def test_list_repos_falls_back_to_user() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.side_effect = [
        _resp({"message": "not found"}, status=404),
        _resp([{"name": "personal-repo"}]),
    ]
    gh = GitHubReadOnlyTools(token="ghp_test", http=fake)
    repos = gh.list_repos("alice")
    assert repos == [{"name": "personal-repo"}]
    paths = [c.args[0] for c in fake.get.call_args_list]
    assert paths == ["/orgs/alice/repos", "/users/alice/repos"]


def test_list_issues_filters_out_pull_requests() -> None:
    """GitHub returns PRs in /issues — we filter them out."""
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _resp([
        {"number": 1, "title": "real issue"},
        {"number": 2, "title": "actually a PR", "pull_request": {"url": "..."}},
        {"number": 3, "title": "another issue"},
    ])
    gh = GitHubReadOnlyTools(token="ghp_test", http=fake)
    issues = gh.list_issues("acme", "agent-kit", state="open")
    assert [i["number"] for i in issues] == [1, 3]


def test_list_issues_validates_state() -> None:
    gh = GitHubReadOnlyTools(token="ghp_test", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError, match="state"):
        gh.list_issues("acme", "agent-kit", state="invalid")


def test_list_issues_with_labels() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _resp([])
    gh = GitHubReadOnlyTools(token="ghp_test", http=fake)
    gh.list_issues("acme", "x", labels=["bug", "security"])
    params = fake.get.call_args.kwargs["params"]
    assert params["labels"] == "bug,security"


def test_get_issue() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _resp({"number": 42, "title": "answer"})
    gh = GitHubReadOnlyTools(token="ghp_test", http=fake)
    issue = gh.get_issue("acme", "x", 42)
    assert issue["title"] == "answer"
    assert fake.get.call_args.args[0] == "/repos/acme/x/issues/42"


def test_get_issue_requires_args() -> None:
    gh = GitHubReadOnlyTools(token="ghp_test", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError):
        gh.get_issue("", "x", 1)
    with pytest.raises(ValueError):
        gh.get_issue("a", "x", 0)


def test_list_pulls() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _resp([{"number": 7, "title": "feat"}])
    gh = GitHubReadOnlyTools(token="ghp_test", http=fake)
    pulls = gh.list_pulls("acme", "x", state="closed", limit=5)
    assert pulls[0]["number"] == 7
    assert fake.get.call_args.args[0] == "/repos/acme/x/pulls"
    assert fake.get.call_args.kwargs["params"]["state"] == "closed"


def test_list_commits_with_date_window() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _resp([{"sha": "abc"}])
    gh = GitHubReadOnlyTools(token="ghp_test", http=fake)
    gh.list_commits("acme", "x", since="2026-01-01T00:00:00Z", until="2026-02-01T00:00:00Z")
    params = fake.get.call_args.kwargs["params"]
    assert params["since"].startswith("2026-01-01")
    assert params["until"].startswith("2026-02-01")


def test_search_code() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.get.return_value = _resp({
        "items": [{"name": "auth.py", "path": "src/auth.py"}],
    })
    gh = GitHubReadOnlyTools(token="ghp_test", http=fake)
    hits = gh.search_code("OAuth2 in:file")
    assert hits and hits[0]["name"] == "auth.py"


def test_factory_handlers() -> None:
    handlers = github_tools(token="ghp_test")
    assert set(handlers) == {
        "github_list_repos",
        "github_list_issues",
        "github_get_issue",
        "github_list_pulls",
        "github_list_commits",
        "github_search_code",
    }
