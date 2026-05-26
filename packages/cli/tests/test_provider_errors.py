from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from rich.console import Console

from ro_claude_kit_agent_patterns import LLMProvider

from ro_claude_kit_cli import runner
from ro_claude_kit_cli.config import CSKConfig


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _HTTPStatusError(Exception):
    """Mimics httpx.HTTPStatusError (has .response.status_code)."""
    def __init__(self, status_code: int) -> None:
        super().__init__(f"{status_code} error")
        self.response = _FakeResp(status_code)


class _BoomProvider(LLMProvider):
    """A real LLMProvider that always raises a 401, for error-path tests."""
    model: str = "boom"

    def complete(self, **kw):
        raise _HTTPStatusError(401)

    def stream(self, **kw):
        raise _HTTPStatusError(401)


def test_friendly_error_401_mentions_key() -> None:
    cfg = CSKConfig(provider="groq")
    msg = runner._friendly_provider_error(_HTTPStatusError(401), cfg)
    assert "401" in msg and "OPENAI_API_KEY" in msg and "ronin doctor" in msg
    assert "Traceback" not in msg


def test_friendly_error_anthropic_key_name() -> None:
    cfg = CSKConfig(provider="anthropic")
    msg = runner._friendly_provider_error(_HTTPStatusError(403), cfg)
    assert "ANTHROPIC_API_KEY" in msg


def test_friendly_error_429() -> None:
    msg = runner._friendly_provider_error(_HTTPStatusError(429), CSKConfig(provider="groq"))
    assert "rate-limited" in msg


def test_friendly_error_connection() -> None:
    class ConnectError(Exception):
        pass
    msg = runner._friendly_provider_error(ConnectError("boom"), CSKConfig(provider="ollama"))
    assert "couldn't reach" in msg


def test_chat_does_not_crash_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider 401 mid-chat shows a clean message and the loop survives."""
    monkeypatch.setenv("OPENAI_API_KEY", "bad")
    config = CSKConfig(provider="groq")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    inputs = iter(["generate me a gta image", ":q"])
    console.input = lambda *a, **k: next(inputs)  # type: ignore[assignment]

    with patch("ro_claude_kit_cli.runner.build_provider", return_value=_BoomProvider()):
        runner.start_chat(config, console=console)  # must NOT raise

    out = buf.getvalue()
    assert "401" in out and "Unauthorized" in out
    assert "Traceback" not in out
    assert "bye" in out  # reached :q cleanly


def test_run_ask_returns_clean_error_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "bad")
    config = CSKConfig(provider="groq")

    with patch("ro_claude_kit_cli.runner.build_provider", return_value=_BoomProvider()):
        result = runner.run_ask(config, "hello", console=None)

    assert result.success is False
    assert "401" in result.output and "Unauthorized" in result.output