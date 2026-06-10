from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from ronin_mcp_servers import SlackReadOnlyTools, slack_tools


def _ok(payload: dict) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = {"ok": True, **payload}
    response.raise_for_status = MagicMock()
    return response


def _err(error: str) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = {"ok": False, "error": error}
    response.raise_for_status = MagicMock()
    return response


def test_list_channels() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _ok({"channels": [{"id": "C1", "name": "general"}]})
    slack = SlackReadOnlyTools(bot_token="xoxb-test", http=fake)

    channels = slack.list_channels(limit=50)
    assert channels == [{"id": "C1", "name": "general"}]
    args, kwargs = fake.post.call_args
    assert args[0] == "/conversations.list"
    assert kwargs["data"]["limit"] == 50
    assert kwargs["headers"]["Authorization"] == "Bearer xoxb-test"


def test_channel_history_requires_id() -> None:
    slack = SlackReadOnlyTools(bot_token="xoxb-test", http=MagicMock(spec=httpx.Client))
    with pytest.raises(ValueError, match="channel_id"):
        slack.channel_history("")


def test_channel_history() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _ok({"messages": [{"text": "hello"}]})
    slack = SlackReadOnlyTools(bot_token="xoxb-test", http=fake, max_limit=10)
    msgs = slack.channel_history("C1", limit=999)
    assert msgs == [{"text": "hello"}]
    assert fake.post.call_args.kwargs["data"]["limit"] == 10  # clamped


def test_search_requires_user_token() -> None:
    slack = SlackReadOnlyTools(bot_token="xoxb-test", http=MagicMock(spec=httpx.Client))
    with pytest.raises(RuntimeError, match="missing token"):
        slack.search_messages("hello")


def test_search_with_user_token() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _ok({"messages": {"matches": [{"text": "hit"}]}})
    slack = SlackReadOnlyTools(
        bot_token="xoxb-test",
        user_token="xoxp-user",
        http=fake,
    )
    matches = slack.search_messages("urgent")
    assert matches == [{"text": "hit"}]
    assert fake.post.call_args.kwargs["headers"]["Authorization"] == "Bearer xoxp-user"


def test_api_error_raises() -> None:
    fake = MagicMock(spec=httpx.Client)
    fake.post.return_value = _err("invalid_auth")
    slack = SlackReadOnlyTools(bot_token="xoxb-test", http=fake)
    with pytest.raises(RuntimeError, match="invalid_auth"):
        slack.list_channels()


def test_factory_handlers() -> None:
    handlers = slack_tools(bot_token="xoxb-test", user_token="xoxp-user")
    assert set(handlers) == {
        "slack_list_channels",
        "slack_channel_history",
        "slack_list_users",
        "slack_search_messages",
    }
