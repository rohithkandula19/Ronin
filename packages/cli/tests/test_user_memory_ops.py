"""Offline tests for explicit memory commands and the Telegram memory bridge.

No network: the Telegram client is a fake that records POSTs, and memory writes
go to a tmp ~/.ronin via the HOME fixture. No LLM/provider is ever built, because
these commands are handled deterministically before the agent runs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli import memory_store
from ronin_cli.telegram_bot import TelegramBot
from ronin_cli.user_memory_ops import MemoryOp, parse_memory_op

VALID_TOKEN = "123456789:AAExampleSecretTokenValue"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ---------- parser ----------

@pytest.mark.parametrize("text,kind,payload", [
    ("remember that I use Groq", "remember", "I use Groq"),
    ("Remember I prefer Python.", "remember", "I prefer Python"),
    ("please remember that my repo is ronin", "remember", "my repo is ronin"),
    ("note that I deploy on Fly.io", "remember", "I deploy on Fly.io"),
    ("keep in mind I work nights", "remember", "I work nights"),
    ("forget that I use Groq", "forget", "I use Groq"),
    ("forget about my old job", "forget", "my old job"),
    ("what do you remember", "list", ""),
    ("what do you remember about me?", "list", ""),
    ("what do you know about me", "list", ""),
    ("list my memories", "list", ""),
    ("show memory", "list", ""),
])
def test_parse_memory_op(text, kind, payload) -> None:
    op = parse_memory_op(text)
    assert op == MemoryOp(kind=kind, payload=payload)


@pytest.mark.parametrize("text", [
    "what time is it",
    "deploy the app please",
    "remember",          # no fact
    "forget",            # no match
    "tell me about the weather",
    "",
])
def test_parse_memory_op_non_memory(text) -> None:
    assert parse_memory_op(text) is None


# ---------- Telegram bridge: deterministic, no model call ----------

class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, getupdates_result: list | None = None) -> None:
        self.calls: list[dict] = []
        self._getupdates_result = getupdates_result or []
        self._served = False

    def post(self, url: str, json: dict | None = None, **kwargs):  # noqa: A002
        self.calls.append({"url": url, "json": json})
        if url.endswith("/getUpdates"):
            if not self._served:
                self._served = True
                return FakeResponse({"ok": True, "result": self._getupdates_result})
            return FakeResponse({"ok": True, "result": []})
        return FakeResponse({"ok": True, "result": {"message_id": 1}})


def _update(text: str, chat_id: int = 42, update_id: int = 100) -> dict:
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id}}}


def _sent_texts(client: FakeClient) -> list[str]:
    return [c["json"]["text"] for c in client.calls if c["url"].endswith("/sendMessage")]


def _boom_agent(text: str) -> str:
    raise AssertionError("agent must not run for an explicit memory command")


def test_remember_command_persists_and_confirms(home: Path) -> None:
    client = FakeClient([_update("remember that I use Groq")])
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42],
                      answer_fn=_boom_agent, http=client)
    bot.poll_once()

    assert "I use Groq" in memory_store.list_memories()
    assert any("i'll remember" in t.lower() for t in _sent_texts(client))


def test_forget_command_removes_and_confirms(home: Path) -> None:
    memory_store.add_memory("I use Groq")
    memory_store.add_memory("I prefer Python")
    client = FakeClient([_update("forget that I use Groq")])
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42],
                      answer_fn=_boom_agent, http=client)
    bot.poll_once()

    texts = memory_store.list_memories()
    assert "I use Groq" not in texts and "I prefer Python" in texts
    assert any("forgot" in t.lower() for t in _sent_texts(client))


def test_what_do_you_remember_lists(home: Path) -> None:
    memory_store.add_memory("User's name is Rohith")
    client = FakeClient([_update("what do you remember about me?")])
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42],
                      answer_fn=_boom_agent, http=client)
    bot.poll_once()

    sent = "\n".join(_sent_texts(client))
    assert "Rohith" in sent


def test_what_do_you_remember_empty(home: Path) -> None:
    client = FakeClient([_update("what do you remember")])
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42],
                      answer_fn=_boom_agent, http=client)
    bot.poll_once()
    assert any("don't remember" in t.lower() for t in _sent_texts(client))


def test_non_memory_message_falls_through_to_agent(home: Path) -> None:
    invoked: list[str] = []

    def agent(text: str) -> str:
        invoked.append(text)
        return "answer"

    client = FakeClient([_update("deploy the app")])
    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42],
                      answer_fn=agent, http=client)
    bot.poll_once()
    assert invoked == ["deploy the app"]
