"""Tests for the faithfulness edit guard wired into the live coding write path.

Everything runs offline: no provider, no keys, no network. The end-to-end tests
drive the real ``run_code_agent`` loop through the in-memory ``FakeProvider``, so
a stub that never actually scores the edit (or a wiring that never calls the
guard) fails them - the write is observed to be held / let through by the guard,
not by a mock.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import patch

from ronin_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ronin_cli.code_faithfulness import (
    EDIT_TOOLS,
    EditGuard,
    gate_feedback,
    proposed_code,
    wrap_after_tool,
    wrap_before_tool,
)
from ronin_cli.code_mode import run_code_agent
from ronin_cli.config import RoninConfig
from ronin_cli.faithfulness_hook import MODE_GATE, MODE_OFF, MODE_WARN

# The file the agent "read" - defines login/make_token/Session and nothing else.
AUTH_BODY = (
    "def login(username, password):\n"
    "    token = make_token(username)\n"
    "    return Session(token=token)\n"
)


# ---------- proposed_code: pulls the new text out of each edit tool ----------

def test_proposed_code_write_file() -> None:
    assert proposed_code("write_file", {"path": "a.py", "content": "x = 1\n"}) == "x = 1\n"


def test_proposed_code_edit_file_uses_new_string() -> None:
    code = proposed_code("edit_file", {"path": "a.py", "old_string": "a", "new_string": "b"})
    assert code == "b"


def test_proposed_code_multi_edit_joins_new_strings() -> None:
    code = proposed_code("multi_edit", {"path": "a.py", "edits": [
        {"old_string": "a", "new_string": "alpha"},
        {"old_string": "b", "new_string": "beta"},
    ]})
    assert "alpha" in code and "beta" in code


def test_proposed_code_non_edit_tool_is_empty() -> None:
    assert proposed_code("read_file", {"path": "a.py"}) == ""


# ---------- EditGuard: real source collection + scoring ----------

def test_guard_inactive_off_mode() -> None:
    assert not EditGuard(mode=MODE_OFF).active
    assert EditGuard(mode=MODE_WARN).active
    assert EditGuard(mode=MODE_GATE).active


def test_guard_collects_only_read_results() -> None:
    g = EditGuard(mode=MODE_WARN)
    g.collect("read_file", {"path": "auth.py"}, AUTH_BODY, False)
    g.collect("write_file", {"path": "x.py"}, "wrote 5 chars", False)  # not evidence
    g.collect("read_file", {"path": "bad.py"}, "ERROR", True)          # errored
    assert len(g.sources) == 1
    assert g.sources[0].origin == "read_file"


def test_guard_flags_ungrounded_edit() -> None:
    g = EditGuard(mode=MODE_GATE)
    g.collect("read_file", {"path": "auth.py"}, AUTH_BODY, False)
    outcome = g.evaluate("write_file", {"path": "auth.py",
                                        "content": "def logout(s):\n    revoke_token(s)\n"})
    assert outcome is not None
    assert "revoke_token" in outcome.report.hallucinated_symbols
    assert outcome.ungrounded
    assert outcome.should_gate  # gate mode -> held


def test_guard_passes_grounded_edit() -> None:
    g = EditGuard(mode=MODE_GATE)
    g.collect("read_file", {"path": "auth.py"}, AUTH_BODY, False)
    outcome = g.evaluate("write_file", {"path": "auth.py",
                                        "content": "session = login(username, password)\n"
                                                   "token = session.token\n"})
    assert outcome is not None
    assert outcome.report.hallucinated_symbols == []
    assert not outcome.ungrounded
    assert not outcome.should_gate


def test_guard_ignores_non_edit_and_empty() -> None:
    g = EditGuard(mode=MODE_WARN)
    assert g.evaluate("run_command", {"command": "ls"}) is None
    assert g.evaluate("write_file", {"path": "a.py", "content": "   "}) is None


def test_gate_feedback_is_plain_ascii_and_names_symbols() -> None:
    g = EditGuard(mode=MODE_GATE)
    g.collect("read_file", {"path": "auth.py"}, AUTH_BODY, False)
    outcome = g.evaluate("write_file", {"path": "auth.py",
                                        "content": "revoke_token(session)\n"})
    msg = gate_feedback(outcome)
    assert msg.isascii()
    assert "revoke_token" in msg
    assert "faithfulness gate" in msg


# ---------- wrap_after_tool / wrap_before_tool composition ----------

def test_wrap_after_tool_feeds_guard_then_inner() -> None:
    g = EditGuard(mode=MODE_WARN)
    seen: list[str] = []
    wrapped = wrap_after_tool(lambda n, a, r, e: seen.append(n), g)
    wrapped("read_file", {"path": "auth.py"}, AUTH_BODY, False)
    assert len(g.sources) == 1          # guard collected it
    assert seen == ["read_file"]        # inner hook still ran


def test_wrap_before_tool_warn_does_not_block() -> None:
    g = EditGuard(mode=MODE_WARN)
    g.collect("read_file", {"path": "auth.py"}, AUTH_BODY, False)
    inner_called: list[str] = []

    def inner(name: str, args: dict):
        inner_called.append(name)
        return True

    gated = wrap_before_tool(inner, g, console=None)
    # An ungrounded write in WARN mode is surfaced but still falls through to the
    # inner gate (which here approves) - warn never blocks.
    verdict = gated("write_file", {"path": "auth.py", "content": "revoke_token(x)\n"})
    assert verdict is True
    assert inner_called == ["write_file"]


def test_wrap_before_tool_gate_holds_ungrounded() -> None:
    g = EditGuard(mode=MODE_GATE)
    g.collect("read_file", {"path": "auth.py"}, AUTH_BODY, False)
    inner_called: list[str] = []
    gated = wrap_before_tool(
        lambda n, a: inner_called.append(n) or True, g, console=None
    )
    # GATE + ungrounded -> held with feedback (a string), inner gate NEVER reached.
    verdict = gated("write_file", {"path": "auth.py", "content": "revoke_token(x)\n"})
    assert isinstance(verdict, str)
    assert "revoke_token" in verdict
    assert inner_called == []  # the edit was held before the human/yolo gate


def test_wrap_before_tool_gate_passes_grounded() -> None:
    g = EditGuard(mode=MODE_GATE)
    g.collect("read_file", {"path": "auth.py"}, AUTH_BODY, False)
    inner_called: list[str] = []
    gated = wrap_before_tool(
        lambda n, a: inner_called.append(n) or True, g, console=None
    )
    grounded = "session = login(username, password)\ntoken = session.token\n"
    verdict = gated("write_file", {"path": "auth.py", "content": grounded})
    assert verdict is True
    assert inner_called == ["write_file"]  # grounded edit reaches the normal gate


def test_inactive_guard_returns_inner_unchanged() -> None:
    g = EditGuard(mode=MODE_OFF)
    inner = lambda n, a: True
    assert wrap_before_tool(inner, g) is inner


# ---------- end-to-end through run_code_agent (real loop, FakeProvider) ----------

def _reads_then_writes(content: str) -> FakeProvider:
    """Read auth.py, then propose writing ``content`` to it, then summarize."""
    return FakeProvider(responses=[
        LLMResponse(
            text="Reading the file.",
            tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "auth.py"})],
            stop_reason="tool_use",
        ),
        LLMResponse(
            text="Applying the change.",
            tool_calls=[ToolCall(id="t2", name="write_file",
                                 arguments={"path": "auth.py", "content": content})],
            stop_reason="tool_use",
        ),
        LLMResponse(text="Done.", stop_reason="end_turn"),
    ])


def test_live_gate_holds_ungrounded_write_even_under_yolo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline proof: in GATE mode an ungrounded write is held in the LIVE
    write path - even with yolo, which would otherwise auto-approve."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "auth.py").write_text(AUTH_BODY, encoding="utf-8")
    config = RoninConfig(provider="anthropic", faithfulness="gate")
    # ungrounded: revoke_token / SecretVault appear in nothing the agent read.
    ungrounded = "def logout(session):\n    SecretVault.revoke_token(session)\n"

    with patch("ronin_cli.code_mode.build_provider", return_value=_reads_then_writes(ungrounded)):
        result = run_code_agent(config, "add logout", root=tmp_path, console=None, yolo=True)

    # The file is UNCHANGED: the guard held the write despite yolo.
    assert (tmp_path / "auth.py").read_text() == AUTH_BODY
    # And the agent saw the faithfulness-gate denial in its trace.
    assert any(
        s.kind == "error" and "faithfulness gate" in str(s.content)
        for s in result.steps
    ), "expected a faithfulness-gate denial step in the trace"


def test_live_gate_allows_grounded_write_under_yolo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grounded write (only symbols the agent read) is NOT held - proving the
    guard discriminates and is not a blanket block."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "auth.py").write_text(AUTH_BODY, encoding="utf-8")
    config = RoninConfig(provider="anthropic", faithfulness="gate")
    grounded = AUTH_BODY + "session = login(username, password)\n"

    with patch("ronin_cli.code_mode.build_provider", return_value=_reads_then_writes(grounded)):
        result = run_code_agent(config, "touch login", root=tmp_path, console=None, yolo=True)

    assert result.success
    # The grounded write went through (yolo auto-approves; guard let it pass).
    assert (tmp_path / "auth.py").read_text() == grounded
    assert not any(
        s.kind == "error" and "faithfulness gate" in str(s.content)
        for s in result.steps
    )


def test_live_off_mode_never_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With faithfulness off (the default), an ungrounded write is NOT held -
    the guard is fully opt-in and zero-overhead by default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "auth.py").write_text(AUTH_BODY, encoding="utf-8")
    config = RoninConfig(provider="anthropic")  # faithfulness defaults to "off"
    ungrounded = "def logout(session):\n    SecretVault.revoke_token(session)\n"

    with patch("ronin_cli.code_mode.build_provider", return_value=_reads_then_writes(ungrounded)):
        result = run_code_agent(config, "add logout", root=tmp_path, console=None, yolo=True)

    # Off mode: the ungrounded write lands (no gate), proving the guard is gated
    # on the config mode, not always-on.
    assert (tmp_path / "auth.py").read_text() == ungrounded
    assert not any(
        s.kind == "error" and "faithfulness gate" in str(s.content)
        for s in result.steps
    )


def test_live_warn_mode_surfaces_but_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """WARN mode surfaces the score but never blocks: the ungrounded write still
    lands under yolo."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "auth.py").write_text(AUTH_BODY, encoding="utf-8")
    config = RoninConfig(provider="anthropic", faithfulness="warn")
    ungrounded = "def logout(session):\n    SecretVault.revoke_token(session)\n"

    with patch("ronin_cli.code_mode.build_provider", return_value=_reads_then_writes(ungrounded)):
        result = run_code_agent(config, "add logout", root=tmp_path, console=None, yolo=True)

    assert (tmp_path / "auth.py").read_text() == ungrounded  # warn never blocks
    assert result.success


def test_edit_tools_are_subset_of_sensitive() -> None:
    """Every tool the edit guard scores is also gated by approval - a write the
    guard cares about can never bypass the human gate."""
    from ronin_cli.code_tools import SENSITIVE_TOOLS
    assert EDIT_TOOLS <= set(SENSITIVE_TOOLS)
