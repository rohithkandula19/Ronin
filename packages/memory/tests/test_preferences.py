from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ronin_memory import UserPreferenceMemory


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


def test_set_get_unset() -> None:
    mem = UserPreferenceMemory()
    mem.set("alice", "tone", "concise")
    mem.set("alice", "timezone", "America/Los_Angeles")
    assert mem.get("alice", "tone") == "concise"
    assert mem.all("alice") == {"tone": "concise", "timezone": "America/Los_Angeles"}
    mem.unset("alice", "tone")
    assert mem.get("alice", "tone") is None


def test_extract_from_message_stores_facts() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp(
        '<facts>[{"key": "tone", "value": "concise"}, '
        '{"key": "timezone", "value": "America/Los_Angeles"}]</facts>'
    )
    mem = UserPreferenceMemory()
    with patch("ronin_memory.preferences.anthropic.Anthropic", return_value=fake_client):
        stored = mem.extract_from_message(
            "alice",
            "I prefer concise responses and I'm in LA.",
        )
    assert ("tone", "concise") in stored
    assert mem.get("alice", "timezone") == "America/Los_Angeles"


def test_extract_handles_garbage() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp("not json at all")
    mem = UserPreferenceMemory()
    with patch("ronin_memory.preferences.anthropic.Anthropic", return_value=fake_client):
        stored = mem.extract_from_message("alice", "hello")
    assert stored == []
    assert mem.all("alice") == {}


def test_extract_filters_invalid_items() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp(
        '<facts>[{"key": "language", "value": "Python"}, '
        '{"value": "missing-key"}, "string-not-object"]</facts>'
    )
    mem = UserPreferenceMemory()
    with patch("ronin_memory.preferences.anthropic.Anthropic", return_value=fake_client):
        stored = mem.extract_from_message("alice", "irrelevant")
    assert stored == [("language", "Python")]
