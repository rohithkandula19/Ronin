"""Offline tests for the multi-channel gateway (`ronin gateway`).

Nothing here touches the network or needs a token: the PURE safety core
(`is_allowed`, `pairing_code`, `gate`) is exercised directly, and routing is
driven through a fake in-memory adapter with a stubbed (no-LLM) agent.
"""
from __future__ import annotations

import pytest

from ronin_cli.gateway import (
    DENIED,
    ENV_PAIRING_SECRET,
    NEEDS_PAIRING,
    RUN,
    DiscordAdapter,
    Gateway,
    GatewayConfigError,
    InboundMessage,
    gate,
    get_pairing_secret,
    get_required_token,
    is_allowed,
    pairing_code,
)


# ---------- is_allowed (fail closed) --------------------------------------

def test_is_allowed_member() -> None:
    assert is_allowed("42", {"42", "7"}) is True


def test_is_allowed_non_member() -> None:
    assert is_allowed("99", {"42", "7"}) is False


def test_is_allowed_empty_allowlist_allows_nobody() -> None:
    # Fail closed: an empty allowlist allows NOBODY (mirrors TelegramBot).
    assert is_allowed("42", set()) is False


def test_is_allowed_stringifies_sender() -> None:
    # Adapters stringify ids, but the core must compare uniformly either way.
    assert is_allowed(42, {"42"}) is True  # type: ignore[arg-type]


# ---------- pairing_code (deterministic, keyed) ---------------------------

def test_pairing_code_is_stable_for_same_input() -> None:
    a = pairing_code("12345", "topsecret")
    b = pairing_code("12345", "topsecret")
    assert a == b  # deterministic: sender + owner derive the identical code


def test_pairing_code_varies_by_sender_and_secret() -> None:
    base = pairing_code("12345", "topsecret")
    assert pairing_code("99999", "topsecret") != base  # different sender
    assert pairing_code("12345", "othersecret") != base  # different secret


def test_pairing_code_shape() -> None:
    code = pairing_code("12345", "topsecret")
    assert len(code) == 8
    assert all(c in "0123456789abcdef" for c in code)  # lowercase hex


def test_pairing_code_requires_secret() -> None:
    # No secret => refuse to emit a guessable code (fail closed).
    with pytest.raises(ValueError):
        pairing_code("12345", "")


# ---------- gate (all three outcomes) -------------------------------------

def test_gate_empty_allowlist_denies_nobody_runs() -> None:
    # The headline fail-closed case: empty allowlist → nobody runs.
    assert gate("42", set()) == DENIED


def test_gate_allowlisted_runs() -> None:
    assert gate("42", {"42"}) == RUN


def test_gate_unknown_sender_needs_pairing() -> None:
    # Populated allowlist + unknown sender → start the pairing handshake.
    assert gate("99", {"42"}) == NEEDS_PAIRING


def test_gate_outcomes_match_verification_snippet() -> None:
    # Matches the task's VERIFY one-liner expectations exactly.
    assert gate("42", set()) == DENIED
    assert gate("42", {"42"}) == RUN
    assert gate("99", {"42"}) == NEEDS_PAIRING


# ---------- env helpers fail closed ---------------------------------------

def test_get_required_token_missing_fails_closed() -> None:
    with pytest.raises(GatewayConfigError):
        get_required_token("SOME_TOKEN", env={})


def test_get_required_token_present() -> None:
    assert get_required_token("SOME_TOKEN", env={"SOME_TOKEN": "abc"}) == "abc"


def test_get_pairing_secret_missing_fails_closed() -> None:
    with pytest.raises(GatewayConfigError):
        get_pairing_secret(env={})


def test_get_pairing_secret_present() -> None:
    assert get_pairing_secret(env={ENV_PAIRING_SECRET: "s3cr3t"}) == "s3cr3t"


def test_discord_adapter_from_env_requires_token() -> None:
    # Fail closed: no DISCORD_BOT_TOKEN → the Discord channel refuses to start.
    with pytest.raises(GatewayConfigError):
        DiscordAdapter.from_env(env={})


def test_discord_adapter_scaffold_is_inert_without_client() -> None:
    # The scaffold is constructible and safe offline: no client → no traffic.
    adapter = DiscordAdapter.from_env(env={"DISCORD_BOT_TOKEN": "tok"})
    assert adapter.poll() == []
    adapter.send(InboundMessage("1", "hi", channel="discord"), "ignored")  # no-op


# ---------- routing through a fake adapter (offline, no LLM) ---------------

class FakeAdapter:
    """In-memory adapter: serves queued inbound messages once, records replies."""

    def __init__(self, name: str, inbound: list[InboundMessage]) -> None:
        self.name = name
        self._inbound = inbound
        self._served = False
        self.sent: list[tuple[str, str]] = []  # (sender_id, reply_text)

    def poll(self) -> list[InboundMessage]:
        if self._served:
            return []
        self._served = True
        return self._inbound

    def send(self, message: InboundMessage, text: str) -> None:
        self.sent.append((message.sender_id, text))


def _msg(sender_id: str, text: str = "what is 2+2?", channel: str = "chat") -> InboundMessage:
    return InboundMessage(sender_id=sender_id, text=text, channel=channel, reply_to=sender_id)


def test_gateway_runs_agent_for_allowlisted_sender() -> None:
    adapter = FakeAdapter("chat", [_msg("42")])
    gw = Gateway(
        adapters=[adapter],
        answer_fn=lambda text: f"answer:{text}",
        allowlists={"chat": {"42"}},
        secret="topsecret",
    )
    assert gw.handle(adapter, _msg("42")) == RUN
    # The agent ran and its answer was sent back.
    assert adapter.sent == [("42", "answer:what is 2+2?")]


def test_gateway_offers_pairing_code_to_unknown_sender() -> None:
    adapter = FakeAdapter("chat", [])
    gw = Gateway(
        adapters=[adapter],
        answer_fn=lambda text: "should-not-run",
        allowlists={"chat": {"42"}},  # populated, but sender 99 is unknown
        secret="topsecret",
    )
    assert gw.handle(adapter, _msg("99")) == NEEDS_PAIRING
    # Reply contains the deterministic pairing code; the agent did NOT run.
    assert len(adapter.sent) == 1
    sender, reply = adapter.sent[0]
    assert sender == "99"
    assert pairing_code("99", "topsecret") in reply
    assert "should-not-run" not in reply


def test_gateway_empty_allowlist_denies_and_never_runs_agent() -> None:
    ran: list[str] = []

    def answer(text: str) -> str:
        ran.append(text)
        return "x"

    adapter = FakeAdapter("chat", [])
    gw = Gateway(
        adapters=[adapter],
        answer_fn=answer,
        allowlists={"chat": set()},  # empty → nobody runs (fail closed)
        secret="topsecret",
    )
    assert gw.handle(adapter, _msg("42")) == DENIED
    assert ran == []          # the read-only agent was never invoked
    assert adapter.sent == []  # silent by default, like telegram_bot


def test_gateway_poll_once_routes_each_message() -> None:
    adapter = FakeAdapter("chat", [_msg("42", "a"), _msg("99", "b")])
    gw = Gateway(
        adapters=[adapter],
        answer_fn=lambda text: f"ok:{text}",
        allowlists={"chat": {"42"}},
        secret="topsecret",
    )
    handled = gw.poll_once()
    assert handled == 2
    # 42 got an agent answer; 99 got a pairing prompt.
    assert ("42", "ok:a") in adapter.sent
    assert any(s == "99" and pairing_code("99", "topsecret") in r for s, r in adapter.sent)


def test_gateway_agent_error_never_crashes_the_loop() -> None:
    def boom(text: str) -> str:
        raise RuntimeError("provider down")

    adapter = FakeAdapter("chat", [])
    gw = Gateway(
        adapters=[adapter],
        answer_fn=boom,
        allowlists={"chat": {"42"}},
        secret="topsecret",
    )
    # The error is swallowed into a short reply; no exception escapes.
    assert gw.handle(adapter, _msg("42")) == RUN
    assert len(adapter.sent) == 1
    assert "error" in adapter.sent[0][1].lower()


def test_gateway_unwired_agent_replies_safely() -> None:
    adapter = FakeAdapter("chat", [])
    gw = Gateway(adapters=[adapter], answer_fn=None, allowlists={"chat": {"42"}}, secret="s")
    assert gw.handle(adapter, _msg("42")) == RUN
    assert "not wired" in adapter.sent[0][1].lower()
