"""Tests for self-verify: run the repo's tests before claiming a task is done.

Everything runs offline. The test runner is mocked - no real subprocess - so a
failing (mocked) suite yields a "NOT verified" verdict, a passing one yields
"verified", and a missing command yields the honest "no tests found" note. The
end-to-end tests drive the real ``run_code_agent`` loop through the in-memory
``FakeProvider`` with ``run_verify`` patched, so the wiring is exercised without
shelling out.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import patch

from ronin_agent_patterns import FakeProvider, LLMResponse, Step, ToolCall
from ronin_cli.code_mode import run_code_agent
from ronin_cli.config import RoninConfig
from ronin_cli.self_verify import (
    append_verification_note,
    changes_were_made,
    render_verdict,
    self_verify_enabled,
    verification_note,
)
from ronin_cli.verify_cmd import Verdict


def _passed(command: str = "uv run pytest -q") -> Verdict:
    return Verdict(passed=True, ran=True, command=command, runner="pytest",
                   summary="3 passed")


def _failed(command: str = "uv run pytest -q") -> Verdict:
    return Verdict(passed=False, ran=True, command=command, runner="pytest",
                   summary="1 failed, 2 passed")


def _no_command() -> Verdict:
    return Verdict(passed=False, ran=False, command="", runner="none",
                   summary="no test command detected for this repo")


def _edit_call(name: str = "write_file") -> Step:
    return Step(kind="tool_call",
                content={"name": name, "input": {"path": "x.py", "content": "x = 1\n"}})


# ---------------- render_verdict: the three honest outcomes ----------------

def test_passing_tests_yield_verified() -> None:
    note = render_verdict(_passed())
    assert note.isascii()
    assert "verified" in note.lower()
    assert "NOT verified" not in note
    assert "uv run pytest -q" in note


def test_failing_tests_yield_not_verified() -> None:
    note = render_verdict(_failed())
    assert note.isascii()
    assert "NOT verified" in note
    assert "not confirmed done" in note.lower()


def test_missing_command_is_honest_no_tests_note() -> None:
    note = render_verdict(_no_command())
    assert note.isascii()
    assert "no tests found to verify" in note
    assert "verified. Ran" not in note  # must not imply success


def test_could_not_run_is_not_verified() -> None:
    v = Verdict(passed=False, ran=False, command="npm test", runner="node",
                summary="command not found")
    note = render_verdict(v)
    assert "NOT verified" in note
    assert "npm test" in note


# ---------------- changes_were_made: only verify when something changed -------

def test_changes_detected_when_edit_tool_ran() -> None:
    assert changes_were_made([_edit_call("write_file")]) is True
    assert changes_were_made([_edit_call("edit_file")]) is True
    assert changes_were_made([_edit_call("multi_edit")]) is True


def test_no_changes_when_only_reads() -> None:
    trace = [Step(kind="tool_call", content={"name": "read_file", "input": {}})]
    assert changes_were_made(trace) is False
    assert changes_were_made([]) is False


# ---------------- verification_note: bounded, mocked runner ------------------

def test_note_runs_once_and_reports_failure() -> None:
    counter = _CallCounter(_failed())
    note = verification_note([_edit_call()], run_verify=counter)
    assert "NOT verified" in note
    assert counter.calls == 1  # exactly ONE verify run (bounded)


def test_note_reports_pass() -> None:
    counter = _CallCounter(_passed())
    note = verification_note([_edit_call()], run_verify=counter)
    assert "verified" in note.lower() and "NOT verified" not in note
    assert counter.calls == 1


def test_note_honest_when_no_tests() -> None:
    counter = _CallCounter(_no_command())
    note = verification_note([_edit_call()], run_verify=counter)
    assert "no tests found to verify" in note


def test_read_only_skips_verify() -> None:
    counter = _CallCounter(_passed())
    note = verification_note([_edit_call()], run_verify=counter, read_only=True)
    assert note == ""
    assert counter.calls == 0  # never shelled out


def test_no_changes_skips_verify() -> None:
    counter = _CallCounter(_passed())
    reads = [Step(kind="tool_call", content={"name": "read_file", "input": {}})]
    note = verification_note(reads, run_verify=counter)
    assert note == ""
    assert counter.calls == 0


def test_runner_error_degrades_to_no_note() -> None:
    def boom(root, timeout=300):
        raise RuntimeError("subprocess blew up")
    note = verification_note([_edit_call()], run_verify=boom)
    assert note == ""  # never raises; degrades to no note


# ---------------- opt-out toggle ----------------

def test_enabled_by_default() -> None:
    assert self_verify_enabled(None)


def test_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RONIN_SELF_VERIFY", "0")
    assert not self_verify_enabled(None)
    counter = _CallCounter(_failed())
    assert verification_note([_edit_call()], run_verify=counter) == ""
    assert counter.calls == 0


def test_disabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RONIN_SELF_VERIFY", raising=False)
    cfg = RoninConfig(provider="anthropic", self_verify=False)
    assert not self_verify_enabled(cfg)


def test_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RONIN_SELF_VERIFY", "1")
    cfg = RoninConfig(provider="anthropic", self_verify=False)
    assert self_verify_enabled(cfg)  # env wins


def test_append_is_noop_when_empty() -> None:
    assert append_verification_note("summary", "") == "summary"
    out = append_verification_note("summary", "VERIFICATION: verified.")
    assert out.startswith("summary")
    assert "VERIFICATION" in out


# ---------- end-to-end through run_code_agent (real loop, FakeProvider) ------

def _write_then_answer(answer_text: str) -> FakeProvider:
    """Write a file, then end the turn with ``answer_text`` as the final answer."""
    return FakeProvider(responses=[
        LLMResponse(
            text="Editing.",
            tool_calls=[ToolCall(id="t1", name="write_file",
                                 arguments={"path": "x.py", "content": "x = 1\n"})],
            stop_reason="tool_use",
        ),
        LLMResponse(text=answer_text, stop_reason="end_turn"),
    ])


def test_live_failing_tests_append_not_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline proof: an edit turn whose (mocked) tests FAIL gets a
    'NOT verified / tests failed' verdict appended - the agent cannot claim
    success blindly. The runner is mocked, so no real subprocess runs."""
    monkeypatch.delenv("RONIN_SELF_VERIFY", raising=False)
    monkeypatch.delenv("RONIN_GROUNDING_CHECK", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic")
    fake = _write_then_answer("Done. Added x.py.")

    with patch("ronin_cli.code_mode.build_provider", return_value=fake), \
         patch("ronin_cli.verify_cmd.run_verify", return_value=_failed()) as rv:
        result = run_code_agent(config, "add x", root=tmp_path, console=None, yolo=True)

    assert "VERIFICATION" in result.output
    assert "NOT verified" in result.output
    assert result.output.startswith("Done. Added x.py.")  # summary preserved
    assert rv.call_count == 1  # exactly one verify run
    assert len(fake.calls) == 2  # no extra model call added


def test_live_passing_tests_append_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RONIN_SELF_VERIFY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic")
    fake = _write_then_answer("Done. Added x.py.")

    with patch("ronin_cli.code_mode.build_provider", return_value=fake), \
         patch("ronin_cli.verify_cmd.run_verify", return_value=_passed()):
        result = run_code_agent(config, "add x", root=tmp_path, console=None, yolo=True)

    assert "VERIFICATION: verified" in result.output
    assert "NOT verified" not in result.output


def test_live_no_tests_is_honest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RONIN_SELF_VERIFY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic")
    fake = _write_then_answer("Done. Added x.py.")

    with patch("ronin_cli.code_mode.build_provider", return_value=fake), \
         patch("ronin_cli.verify_cmd.run_verify", return_value=_no_command()):
        result = run_code_agent(config, "add x", root=tmp_path, console=None, yolo=True)

    assert "no tests found to verify" in result.output


def test_live_opt_out_via_env_suppresses_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RONIN_SELF_VERIFY", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic")
    fake = _write_then_answer("Done. Added x.py.")

    with patch("ronin_cli.code_mode.build_provider", return_value=fake), \
         patch("ronin_cli.verify_cmd.run_verify", return_value=_failed()) as rv:
        result = run_code_agent(config, "add x", root=tmp_path, console=None, yolo=True)

    assert "VERIFICATION" not in result.output
    assert rv.call_count == 0  # never ran the suite


def test_live_read_only_run_has_no_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only / plan run never edits, so there is nothing to verify."""
    monkeypatch.delenv("RONIN_SELF_VERIFY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic")
    fake = FakeProvider(responses=[LLMResponse(text="Looks fine.", stop_reason="end_turn")])

    with patch("ronin_cli.code_mode.build_provider", return_value=fake), \
         patch("ronin_cli.verify_cmd.run_verify", return_value=_failed()) as rv:
        result = run_code_agent(config, "review", root=tmp_path, console=None,
                                yolo=True, read_only=True)

    assert "VERIFICATION" not in result.output
    assert rv.call_count == 0


class _CallCounter:
    """A fake run_verify that returns a fixed Verdict and counts invocations,
    proving self-verify shells out at most once (bounded)."""

    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        self.calls = 0

    def __call__(self, root, timeout: int = 300) -> Verdict:
        self.calls += 1
        return self.verdict
