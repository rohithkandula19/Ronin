"""Tests for the faithfulness post-answer hook and edit guard (CLI wiring).

Everything runs offline: no provider, no keys, no network. The end-to-end tests
drive the real ``run_agent`` loop through the in-memory FakeProvider, so a stub
that always reports "grounded" fails them.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from ronin_agent_patterns import FakeProvider, LLMResponse, Tool, ToolCall
from ronin_cli.config import RoninConfig
from ronin_cli.faithfulness_hook import (
    MODE_GATE,
    MODE_OFF,
    MODE_WARN,
    evaluate_answer,
    evaluate_answer_from_trace,
    evaluate_edit,
    mode_of,
)
from ronin_hardening import Source

# A tiny "codebase" the agent supposedly read.
AUTH_SOURCE = Source(
    origin="read_file",
    text=(
        "def login(username, password):\n"
        "    token = make_token(username)\n"
        "    return Session(token=token)\n"
    ),
)


# ---------- mode resolution ----------

def test_mode_of_defaults_off() -> None:
    assert mode_of(None) == MODE_OFF
    assert mode_of(RoninConfig()) == MODE_OFF


def test_mode_of_reads_config() -> None:
    assert mode_of(RoninConfig(faithfulness="warn")) == MODE_WARN
    assert mode_of(RoninConfig(faithfulness="gate")) == MODE_GATE


def test_config_normalizes_bad_mode() -> None:
    # An unknown value in the config must not crash; it falls back to off.
    assert RoninConfig(faithfulness="BOGUS").faithfulness == MODE_OFF
    assert RoninConfig(faithfulness="WARN").faithfulness == MODE_WARN


# ---------- evaluate_answer: real grounding signal ----------

def test_grounded_answer_is_not_flagged() -> None:
    outcome = evaluate_answer(
        "The login function calls make_token and returns a Session.",
        [AUTH_SOURCE],
        mode=MODE_WARN,
    )
    assert not outcome.ungrounded
    assert not outcome.abstain
    assert not outcome.should_warn
    assert outcome.report.score >= 0.65


def test_ungrounded_answer_is_flagged() -> None:
    outcome = evaluate_answer(
        "The login function calls refresh_oauth_token to rotate the credentials.",
        [AUTH_SOURCE],
        mode=MODE_WARN,
    )
    assert outcome.ungrounded
    assert "refresh_oauth_token" in outcome.report.hallucinated_symbols
    assert outcome.should_warn


def test_grounded_and_ungrounded_diverge() -> None:
    """Guards against an 'always grounded' stub: the two must genuinely differ."""
    good = evaluate_answer("login returns a Session and calls make_token.", [AUTH_SOURCE])
    bad = evaluate_answer("delete_everything wipes the QuantumDatabase via warp_drive().", [AUTH_SOURCE])
    assert good.report.score > bad.report.score
    assert not good.ungrounded
    assert bad.ungrounded


def test_abstain_with_no_sources() -> None:
    outcome = evaluate_answer("login returns a Session.", [], mode=MODE_WARN)
    assert outcome.abstain
    assert not outcome.ungrounded  # abstain is "no evidence", not "ungrounded"
    assert outcome.should_warn  # surfaced in warn mode


def test_off_mode_never_warns_or_gates() -> None:
    outcome = evaluate_answer(
        "calls refresh_oauth_token", [AUTH_SOURCE], mode=MODE_OFF
    )
    assert outcome.ungrounded  # the verdict still computed
    assert not outcome.should_warn
    assert not outcome.should_gate


def test_gate_mode_gates_only_on_problem() -> None:
    clean = evaluate_answer("login calls make_token and returns a Session.", [AUTH_SOURCE], mode=MODE_GATE)
    assert not clean.should_gate
    bad = evaluate_answer("login calls refresh_oauth_token.", [AUTH_SOURCE], mode=MODE_GATE)
    assert bad.should_gate


# ---------- edit guard ----------

def test_edit_guard_flags_ungrounded_write() -> None:
    outcome = evaluate_edit(
        "def logout(session):\n    revoke_token(session.token)\n",
        [AUTH_SOURCE],
        mode=MODE_WARN,
    )
    assert "revoke_token" in outcome.report.hallucinated_symbols
    assert outcome.ungrounded


def test_edit_guard_clean_grounded_write() -> None:
    outcome = evaluate_edit(
        "session = login(username, password)\ntoken = session.token\n",
        [AUTH_SOURCE],
        mode=MODE_WARN,
    )
    assert outcome.report.hallucinated_symbols == []
    assert not outcome.ungrounded


# ---------- from-trace ----------

def test_evaluate_answer_from_trace_pulls_read_sources() -> None:
    trace = [
        {"kind": "tool_result", "content": {"name": "read_file", "result": AUTH_SOURCE.text, "is_error": False}},
        {"kind": "final", "content": "done"},
    ]
    outcome = evaluate_answer_from_trace(
        "login calls make_token and returns a Session.", trace, mode=MODE_WARN
    )
    assert outcome.report.n_sources == 1
    assert not outcome.ungrounded


def test_summary_is_plain_ascii() -> None:
    outcome = evaluate_answer("login calls refresh_oauth_token.", [AUTH_SOURCE])
    s = outcome.summary
    assert s.isascii()
    assert "faithfulness" in s


# ---------- end-to-end through run_agent (real loop, FakeProvider) ----------

def _read_tool(body: str) -> Tool:
    def read_file(path: str) -> str:
        return body

    return Tool(
        name="read_file",
        description="Read a file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=read_file,
    )


def _provider_reads_then_says(answer: str, body: str) -> FakeProvider:
    return FakeProvider(responses=[
        LLMResponse(
            tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "auth.py"})],
            stop_reason="tool_use",
        ),
        LLMResponse(text=answer, stop_reason="end_turn"),
    ])


def test_run_agent_attaches_grounded_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    from ronin_cli.agent_mode import run_agent

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(demo_mode=True, provider="anthropic")
    body = "def login(u, p):\n    return make_token(u)\n"
    provider = _provider_reads_then_says(
        "The login function calls make_token and returns its result.", body
    )

    with patch("ronin_cli.agent_mode.build_provider", return_value=provider), \
         patch("ronin_cli.agent_mode.build_tools", return_value=[_read_tool(body)]):
        result = run_agent(config, "what does login do?", console=None, faithfulness=MODE_WARN)

    assert result.faithfulness is not None
    assert result.faithfulness.report.n_sources == 1
    assert not result.faithfulness.ungrounded


def test_run_agent_catches_hallucination(monkeypatch: pytest.MonkeyPatch) -> None:
    from ronin_cli.agent_mode import run_agent

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(demo_mode=True, provider="anthropic")
    body = "def login(u, p):\n    return make_token(u)\n"
    provider = _provider_reads_then_says(
        "login calls make_token and then audit_log_to_splunk for compliance.", body
    )

    with patch("ronin_cli.agent_mode.build_provider", return_value=provider), \
         patch("ronin_cli.agent_mode.build_tools", return_value=[_read_tool(body)]):
        result = run_agent(config, "explain login", console=None, faithfulness=MODE_GATE)

    assert result.faithfulness is not None
    assert result.faithfulness.ungrounded
    assert "audit_log_to_splunk" in result.faithfulness.report.hallucinated_symbols
    assert result.faithfulness.should_gate


def test_run_agent_off_skips_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    from ronin_cli.agent_mode import run_agent

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(demo_mode=True, provider="anthropic")
    body = "def login(u, p):\n    return make_token(u)\n"
    provider = _provider_reads_then_says("login calls refresh_oauth_token.", body)

    with patch("ronin_cli.agent_mode.build_provider", return_value=provider), \
         patch("ronin_cli.agent_mode.build_tools", return_value=[_read_tool(body)]):
        result = run_agent(config, "explain login", console=None, faithfulness=MODE_OFF)

    # Off mode: the hook never runs, so no outcome is attached.
    assert result.faithfulness is None
