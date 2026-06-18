"""Offline tests for the Telegram bridge (`ronin telegram`).

Every Telegram HTTP call is mocked via an injected fake client; no test touches
the network. The agent is a stub so no LLM/provider is needed.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app
from ronin_cli.telegram_bot import (
    MAX_MESSAGE_CHARS,
    REDACTED,
    TelegramBot,
    TelegramConfigError,
    _chunk_text,
    get_bot_token,
    parse_allowed_chat_ids,
    redact_token,
)

runner = CliRunner()

VALID_TOKEN = "123456789:AAExampleSecretTokenValue"


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Records POSTs and returns a queued/templated response per method."""

    def __init__(self, getupdates_result: list | None = None) -> None:
        self.calls: list[dict] = []
        self._getupdates_result = getupdates_result or []
        self._getupdates_served = False

    def post(self, url: str, json: dict | None = None, **kwargs):  # noqa: A002
        self.calls.append({"url": url, "json": json})
        if url.endswith("/getMe"):
            return FakeResponse({"ok": True, "result": {"username": "ronin_test_bot"}})
        if url.endswith("/getUpdates"):
            # Serve the queued updates once, then empty (so loops can terminate).
            if not self._getupdates_served:
                self._getupdates_served = True
                return FakeResponse({"ok": True, "result": self._getupdates_result})
            return FakeResponse({"ok": True, "result": []})
        if url.endswith("/sendMessage"):
            return FakeResponse({"ok": True, "result": {"message_id": 1}})
        return FakeResponse({"ok": True, "result": {}})

    # convenience accessors -------------------------------------------------
    def sent_messages(self) -> list[dict]:
        return [c["json"] for c in self.calls if c["url"].endswith("/sendMessage")]


def _update(chat_id: int, text: str, update_id: int = 100) -> dict:
    return {
        "update_id": update_id,
        "message": {"text": text, "chat": {"id": chat_id}},
    }


# ---------- token validation / fail-closed --------------------------------

def test_get_bot_token_missing_fails_closed() -> None:
    with pytest.raises(TelegramConfigError):
        get_bot_token(env={})


def test_get_bot_token_malformed_fails_closed() -> None:
    with pytest.raises(TelegramConfigError):
        get_bot_token(env={"TELEGRAM_BOT_TOKEN": "not-a-token"})


def test_get_bot_token_valid() -> None:
    assert get_bot_token(env={"TELEGRAM_BOT_TOKEN": VALID_TOKEN}) == VALID_TOKEN


def test_parse_allowed_chat_ids_merges_and_dedups() -> None:
    ids = parse_allowed_chat_ids("10, 20, oops, 10", config_ids=[20, 30])
    assert ids == [10, 20, 30]


def test_parse_allowed_chat_ids_empty() -> None:
    assert parse_allowed_chat_ids(None) == []
    assert parse_allowed_chat_ids("") == []


# ---------- 1. allowed chat -> agent invoked, answer sent ------------------

def test_allowed_chat_runs_agent_and_sends_answer() -> None:
    invoked: list[str] = []

    def fake_agent(text: str) -> str:
        invoked.append(text)
        return f"answer to: {text}"

    client = FakeClient(getupdates_result=[_update(42, "hello ronin")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=fake_agent,
        http=client,
    )
    bot.poll_once()

    assert invoked == ["hello ronin"], "agent should be invoked once with the text"
    sent = client.sent_messages()
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 42
    assert sent[0]["text"] == "answer to: hello ronin"


# ---------- 2. non-allowed chat -> agent NOT invoked, no answer ------------

def test_non_allowed_chat_is_ignored() -> None:
    invoked: list[str] = []

    def fake_agent(text: str) -> str:
        invoked.append(text)
        return "should not happen"

    client = FakeClient(getupdates_result=[_update(999, "let me in")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],  # 999 is not allowed
        answer_fn=fake_agent,
        http=client,
    )
    bot.poll_once()

    assert invoked == [], "agent must NOT run for a non-allowed chat"
    assert client.sent_messages() == [], "no message should be sent to a non-allowed chat"


# ---------- 3. empty allowlist -> onboarding reply only -------------------

def test_empty_allowlist_replies_only_with_chat_id() -> None:
    invoked: list[str] = []

    def fake_agent(text: str) -> str:
        invoked.append(text)
        return "should not happen"

    client = FakeClient(getupdates_result=[_update(777, "hi")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[],  # empty: nobody runs the agent
        answer_fn=fake_agent,
        http=client,
    )
    bot.poll_once()

    assert invoked == [], "agent must NOT run when the allowlist is empty"
    sent = client.sent_messages()
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 777
    assert "your chat id is 777" in sent[0]["text"]
    assert "TELEGRAM_ALLOWED_CHAT_IDS" in sent[0]["text"]


# ---------- 4. missing token -> command fails closed, never polls ----------

def test_cli_missing_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    # If the command ever tried to poll, this would explode loudly.
    def explode(*_a, **_k):
        raise AssertionError("must not construct a bot or poll without a token")

    monkeypatch.setattr("ronin_cli.telegram_bot.TelegramBot", explode)

    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code != 0
    assert "TELEGRAM_BOT_TOKEN" in r.stdout


def test_cli_malformed_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bogus")
    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code != 0


# ---------- 5. long answer is chunked across multiple sendMessage ----------

def test_long_answer_is_chunked() -> None:
    long_text = "x" * (MAX_MESSAGE_CHARS * 2 + 500)

    def fake_agent(_text: str) -> str:
        return long_text

    client = FakeClient(getupdates_result=[_update(42, "give me a long one")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=fake_agent,
        http=client,
    )
    bot.poll_once()

    sent = client.sent_messages()
    assert len(sent) >= 3, "a >2x-cap answer must span multiple sendMessage calls"
    assert all(len(m["text"]) <= MAX_MESSAGE_CHARS for m in sent)
    # the reassembled text matches the original (newline-stripped chunking ok here)
    assert "".join(m["text"] for m in sent) == long_text


def test_chunk_text_prefers_newline_boundaries() -> None:
    text = "a" * 10 + "\n" + "b" * (MAX_MESSAGE_CHARS)
    chunks = _chunk_text(text, MAX_MESSAGE_CHARS)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 10
    assert chunks[1] == "b" * MAX_MESSAGE_CHARS


# ---------- offset advances so messages are not reprocessed ----------------

def test_offset_advances_past_handled_updates() -> None:
    client = FakeClient(getupdates_result=[_update(42, "hi", update_id=555)])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=lambda t: "ok",
        http=client,
    )
    bot.poll_once()
    assert bot._offset == 556, "offset must be one past the highest update_id seen"


# ---------- getMe surfaces the username ------------------------------------

def test_get_me_returns_username() -> None:
    client = FakeClient()
    bot = TelegramBot(token=VALID_TOKEN, http=client)
    me = bot.get_me()
    assert me["username"] == "ronin_test_bot"


# ---------- token never leaks via logs or error output ---------------------

def test_redact_token_masks_token_in_message() -> None:
    url = f"https://api.telegram.org/bot{VALID_TOKEN}/getUpdates"
    msg = f"Client error '429 Too Many Requests' for url '{url}'"
    out = redact_token(msg, VALID_TOKEN)
    assert VALID_TOKEN not in out
    assert REDACTED in out


def test_redact_token_noops_on_empty_token() -> None:
    # An empty/unknown token must not blank out unrelated text.
    msg = "some error with no token"
    assert redact_token(msg, "") == msg


def test_run_forever_redacts_token_in_poll_error_log(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """An httpx error during polling must be logged with the token redacted.

    The real httpx.HTTPStatusError str() contains the full request URL, which
    embeds the token. We assert the bot scrubs it before logging.
    """
    import httpx

    url = f"https://api.telegram.org/bot{VALID_TOKEN}/getUpdates"
    request = httpx.Request("POST", url)
    response = httpx.Response(429, request=request)

    # Sanity: the raw exception string really does carry the token.
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as raw:
        assert VALID_TOKEN in str(raw)

    class RaisingClient:
        def post(self, url: str, json=None, **kwargs):  # noqa: A002
            req = httpx.Request("POST", url)
            resp = httpx.Response(429, request=req)
            resp.raise_for_status()  # raises HTTPStatusError carrying the token

    # Make run_forever do exactly one error iteration, then stop the loop.
    sleeps: list[float] = []

    def fake_sleep(_seconds: float) -> None:
        sleeps.append(_seconds)
        raise KeyboardInterrupt  # break out of the while True after one error

    monkeypatch.setattr("ronin_cli.telegram_bot._sleep", fake_sleep)

    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42], http=RaisingClient())

    import logging

    with caplog.at_level(logging.WARNING, logger="ronin.telegram"):
        with pytest.raises(KeyboardInterrupt):
            bot.run_forever()

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert joined, "expected a warning log for the poll error"
    assert VALID_TOKEN not in joined, "token must not appear in poll error logs"
    assert REDACTED in joined


def test_cli_getme_failure_redacts_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A startup getMe failure must not print the token to the terminal."""
    import httpx

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TOKEN)

    def failing_get_me(self):
        url = f"https://api.telegram.org/bot{self.token}/getMe"
        req = httpx.Request("POST", url)
        resp = httpx.Response(401, request=req)
        resp.raise_for_status()

    monkeypatch.setattr(
        "ronin_cli.telegram_bot.TelegramBot.get_me", failing_get_me
    )

    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code != 0
    assert "getMe failed" in r.stdout
    assert VALID_TOKEN not in r.stdout, "token must not be printed on getMe failure"
    assert REDACTED in r.stdout
