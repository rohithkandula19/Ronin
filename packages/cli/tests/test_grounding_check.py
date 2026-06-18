"""Tests for the post-answer grounding check (faithfulness-in-coding).

Everything runs offline: no provider, no keys, no network, no model call. The
end-to-end tests drive the real ``run_code_agent`` loop through the in-memory
``FakeProvider``, so a stub that never actually checks the answer (or a wiring
that never runs the check) fails them - the note is observed to ride along on
``res.output`` only when the answer really references something unread.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import patch

from ronin_agent_patterns import FakeProvider, LLMResponse, Step, ToolCall
from ronin_cli.code_mode import run_code_agent
from ronin_cli.config import RoninConfig
from ronin_cli.grounding_check import (
    append_grounding_note,
    flag_hallucinated_symbols,
    grounding_enabled,
    grounding_note,
    render_note,
)

# The file the agent "read" - defines login / make_token / Session, nothing else.
AUTH_BODY = (
    "def login(username, password):\n"
    "    token = make_token(username)\n"
    "    return Session(token=token)\n"
)


def _read_step(path: str, body: str) -> Step:
    """A trace step shaped like the ReAct loop's read_file tool_result."""
    return Step(
        kind="tool_result",
        content={"name": "read_file", "result": body, "is_error": False},
    )


# ---------- flag_hallucinated_symbols: the core, against a real trace ----------

def test_flags_symbol_absent_from_everything_read() -> None:
    trace = [_read_step("auth.py", AUTH_BODY)]
    answer = "I added a call to revoke_token() to clear the session."
    flagged = flag_hallucinated_symbols(answer, trace)
    assert "revoke_token" in flagged


def test_does_not_flag_symbols_present_in_read_files() -> None:
    trace = [_read_step("auth.py", AUTH_BODY)]
    answer = "login() builds a Session from make_token(), so the token is set."
    flagged = flag_hallucinated_symbols(answer, trace)
    assert flagged == []


def test_no_evidence_abstains_rather_than_flagging_everything() -> None:
    # No read steps at all: we cannot tell grounded from invented, so flag nothing.
    answer = "I called totally_made_up_function() here."
    assert flag_hallucinated_symbols(answer, []) == []


def test_symbol_the_agent_wrote_this_turn_is_grounded() -> None:
    # The agent read auth.py, then WROTE a new helper. Referencing the helper it
    # just wrote in the answer must not be flagged - it now exists.
    trace = [
        _read_step("auth.py", AUTH_BODY),
        Step(
            kind="tool_call",
            content={
                "name": "write_file",
                "input": {
                    "path": "auth.py",
                    "content": "def logout(session):\n    drop_session(session)\n",
                },
            },
        ),
    ]
    answer = "I added logout() which calls drop_session() to end the session."
    assert flag_hallucinated_symbols(answer, trace) == []


def test_file_path_on_disk_is_grounded(tmp_path: Path) -> None:
    # A path the answer claims is grounded when it really exists in the worktree.
    (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")
    trace = [_read_step("auth.py", AUTH_BODY)]
    answer = "I edited real.py to set x."
    assert flag_hallucinated_symbols(answer, trace, root=tmp_path) == []


def test_file_path_not_on_disk_is_flagged(tmp_path: Path) -> None:
    trace = [_read_step("auth.py", AUTH_BODY)]
    answer = "I edited nonexistent_module.py to fix the bug."
    flagged = flag_hallucinated_symbols(answer, trace, root=tmp_path)
    assert "nonexistent_module.py" in flagged


# ---------- grounding_note / render_note / enabled toggle ----------

def test_grounding_note_flags_and_is_plain_ascii() -> None:
    trace = [_read_step("auth.py", AUTH_BODY)]
    note = grounding_note("Calls revoke_token() now.", trace)
    assert note
    assert note.isascii()
    assert "revoke_token" in note
    assert "grounding check" in note


def test_grounding_note_empty_for_grounded_answer() -> None:
    trace = [_read_step("auth.py", AUTH_BODY)]
    assert grounding_note("login() returns a Session.", trace) == ""


def test_render_note_names_a_few_and_counts_the_rest() -> None:
    note = render_note(["aa", "bb", "cc", "dd", "ee", "ff", "gg"])
    assert "and 2 more" in note
    assert note.isascii()


def test_grounding_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RONIN_GROUNDING_CHECK", "0")
    assert not grounding_enabled(None)
    trace = [_read_step("auth.py", AUTH_BODY)]
    assert grounding_note("Calls revoke_token() now.", trace) == ""


def test_grounding_enabled_by_default() -> None:
    assert grounding_enabled(None)


def test_grounding_disabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RONIN_GROUNDING_CHECK", raising=False)
    cfg = RoninConfig(provider="anthropic", grounding_check=False)
    assert not grounding_enabled(cfg)
    trace = [_read_step("auth.py", AUTH_BODY)]
    assert grounding_note("Calls revoke_token() now.", trace, config=cfg) == ""


def test_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RONIN_GROUNDING_CHECK", "1")
    cfg = RoninConfig(provider="anthropic", grounding_check=False)
    assert grounding_enabled(cfg)  # env wins over the config flag


def test_append_grounding_note_is_noop_when_empty() -> None:
    assert append_grounding_note("answer", "") == "answer"
    out = append_grounding_note("answer", "grounding check: ...")
    assert out.startswith("answer")
    assert "grounding check" in out


# ---------- end-to-end through run_code_agent (real loop, FakeProvider) --------

def _read_then_answer(answer_text: str) -> FakeProvider:
    """Read auth.py, then end the turn with ``answer_text`` as the final answer."""
    return FakeProvider(responses=[
        LLMResponse(
            text="Reading the file.",
            tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "auth.py"})],
            stop_reason="tool_use",
        ),
        LLMResponse(text=answer_text, stop_reason="end_turn"),
    ])


def test_live_answer_with_hallucinated_symbol_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline proof: an answer naming a function that exists in nothing the
    agent read gets a grounding-check note appended to res.output - on by default,
    with no extra model call (FakeProvider has exactly the two scripted turns)."""
    monkeypatch.delenv("RONIN_GROUNDING_CHECK", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "auth.py").write_text(AUTH_BODY, encoding="utf-8")
    config = RoninConfig(provider="anthropic")  # grounding_check defaults to True
    answer = "Done. I wired in revoke_token() to clear the session on logout."

    fake = _read_then_answer(answer)
    with patch("ronin_cli.code_mode.build_provider", return_value=fake):
        result = run_code_agent(config, "add logout", root=tmp_path, console=None, yolo=True)

    assert "grounding check" in result.output
    assert "revoke_token" in result.output
    # No extra provider call beyond the two scripted turns => no model call added.
    assert len(fake.calls) == 2


def test_live_grounded_answer_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An answer that only references real symbols from the read file gets NO
    note - the check discriminates and is not a blanket warning."""
    monkeypatch.delenv("RONIN_GROUNDING_CHECK", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "auth.py").write_text(AUTH_BODY, encoding="utf-8")
    config = RoninConfig(provider="anthropic")
    answer = "Confirmed: login() returns a Session built from make_token()."

    fake = _read_then_answer(answer)
    with patch("ronin_cli.code_mode.build_provider", return_value=fake):
        result = run_code_agent(config, "explain login", root=tmp_path, console=None, yolo=True)

    assert "grounding check" not in result.output
    assert result.output == answer
    assert len(fake.calls) == 2


def test_live_opt_out_via_env_suppresses_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With RONIN_GROUNDING_CHECK=0 the note is suppressed even on a clearly
    ungrounded answer - the check is opt-out-able."""
    monkeypatch.setenv("RONIN_GROUNDING_CHECK", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "auth.py").write_text(AUTH_BODY, encoding="utf-8")
    config = RoninConfig(provider="anthropic")
    answer = "Done. I wired in revoke_token() to clear the session."

    fake = _read_then_answer(answer)
    with patch("ronin_cli.code_mode.build_provider", return_value=fake):
        result = run_code_agent(config, "add logout", root=tmp_path, console=None, yolo=True)

    assert "grounding check" not in result.output
    assert result.output == answer
