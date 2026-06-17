"""Tests for the OPTIONAL Telegram adapter.

The adapter is off by default and never required to run the generic webhook.
These tests cover the pure parsing/enable logic and prove a mounted webhook
really runs the agent offline (the outbound Telegram POST is patched, so no
network is touched).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ronin_cli.config import RoninConfig
from ronin_cli.gateway import make_gateway_app
from ronin_cli.gateway_telegram import (
    mount_telegram,
    parse_update,
    telegram_enabled,
)


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_disabled_by_default() -> None:
    assert telegram_enabled({}) is False


def test_flag_without_token_stays_disabled() -> None:
    assert telegram_enabled({"RONIN_GATEWAY_TELEGRAM": "1"}) is False


def test_enabled_only_with_flag_and_token() -> None:
    assert telegram_enabled({"RONIN_GATEWAY_TELEGRAM": "1", "TELEGRAM_BOT_TOKEN": "abc"}) is True
    assert telegram_enabled({"RONIN_GATEWAY_TELEGRAM": "0", "TELEGRAM_BOT_TOKEN": "abc"}) is False


def test_parse_update_message() -> None:
    chat_id, text = parse_update({"message": {"chat": {"id": 42}, "text": "hi there"}})
    assert chat_id == 42
    assert text == "hi there"


def test_parse_update_edited_and_empty() -> None:
    chat_id, text = parse_update({"edited_message": {"chat": {"id": 7}, "text": "edited"}})
    assert (chat_id, text) == (7, "edited")
    # A sticker / service message with no text → no usable input.
    assert parse_update({"message": {"chat": {"id": 7}}}) == (7, "")
    assert parse_update({}) == (None, "")


def test_mounted_webhook_runs_agent_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Telegram update drives the real agent and triggers a reply send."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        "ronin_cli.gateway_telegram.send_message",
        lambda token, chat_id, text, **kw: sent.append((token, chat_id, text)) or True,
    )

    config = RoninConfig(demo_mode=True)
    app = make_gateway_app(config)
    mount_telegram(app, token="TESTTOKEN")
    client = TestClient(app)

    r = client.post(
        "/telegram/TESTTOKEN",
        json={"message": {"chat": {"id": 99}, "text": "list our customers"}},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # The agent ran and a reply was dispatched to the right chat.
    assert len(sent) == 1
    token, chat_id, text = sent[0]
    assert token == "TESTTOKEN"
    assert chat_id == 99
    assert text  # real reply content
    # The session is namespaced by chat id and recorded history.
    assert app.state.session_store.turns("telegram:99") == 2


def test_mounted_webhook_ignores_textless_update(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list = []
    monkeypatch.setattr(
        "ronin_cli.gateway_telegram.send_message",
        lambda *a, **k: sent.append(a) or True,
    )
    config = RoninConfig(demo_mode=True)
    app = make_gateway_app(config)
    mount_telegram(app, token="TESTTOKEN")
    client = TestClient(app)

    r = client.post("/telegram/TESTTOKEN", json={"message": {"chat": {"id": 5}}})
    assert r.status_code == 200
    assert sent == []  # nothing to reply to
