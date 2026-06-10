"""Tests for the red-to-green repair loop."""
from __future__ import annotations

from pathlib import Path

from ronin_cli import repair
from ronin_cli.verify_cmd import Verdict


def _verdict(passed: bool, ran: bool = True) -> Verdict:
    return Verdict(passed=passed, ran=ran, command="make test", runner="make",
                   summary="...", output="boom")


def test_already_green_does_not_call_agent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(repair, "run_verify", lambda root, timeout=600: _verdict(True))
    calls: list = []
    v = repair.repair_to_green(tmp_path, lambda p: calls.append(p))
    assert v.passed and calls == []     # nothing to repair → agent never invoked


def test_no_test_command_is_a_no_op(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(repair, "run_verify",
                        lambda root, timeout=600: _verdict(False, ran=False))
    calls: list = []
    v = repair.repair_to_green(tmp_path, lambda p: calls.append(p))
    assert not v.passed and not v.ran and calls == []   # untested repo ≠ broken


def test_repairs_until_green(tmp_path: Path, monkeypatch) -> None:
    # red, red, then green → two repair attempts, stops on green
    seq = iter([_verdict(False), _verdict(False), _verdict(True)])
    monkeypatch.setattr(repair, "run_verify", lambda root, timeout=600: next(seq))
    monkeypatch.setattr(repair, "_git_diff", lambda root: "diff")
    calls: list = []
    v = repair.repair_to_green(tmp_path, lambda p: calls.append(p), rounds=3)
    assert v.passed
    assert len(calls) == 2                     # exactly the two failing rounds
    assert "FAILED" in calls[0] and "make test" in calls[0]


def test_gives_up_after_rounds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(repair, "run_verify", lambda root, timeout=600: _verdict(False))
    monkeypatch.setattr(repair, "_git_diff", lambda root: "diff")
    calls: list = []
    v = repair.repair_to_green(tmp_path, lambda p: calls.append(p), rounds=2)
    assert not v.passed
    assert len(calls) == 2                      # capped at `rounds` attempts


def test_on_round_callback_fires(tmp_path: Path, monkeypatch) -> None:
    seq = iter([_verdict(False), _verdict(True)])
    monkeypatch.setattr(repair, "run_verify", lambda root, timeout=600: next(seq))
    monkeypatch.setattr(repair, "_git_diff", lambda root: "diff")
    rounds: list = []
    repair.repair_to_green(tmp_path, lambda p: None,
                           on_round=lambda n, v: rounds.append(n))
    assert rounds == [1]                         # one failing round before green


def test_repair_prompt_includes_failure_and_diff(tmp_path: Path, monkeypatch) -> None:
    seq = iter([_verdict(False), _verdict(True)])
    monkeypatch.setattr(repair, "run_verify", lambda root, timeout=600: next(seq))
    monkeypatch.setattr(repair, "_git_diff", lambda root: "DIFF-MARKER")
    captured: list = []
    repair.repair_to_green(tmp_path, lambda p: captured.append(p))
    prompt = captured[0]
    assert "boom" in prompt              # failing output
    assert "DIFF-MARKER" in prompt       # current diff
    assert "weaken" in prompt and "delete tests" in prompt   # guardrail


# ---- review fixes: deletion guard, ran=False break, diff cap ----

def test_refuses_green_when_a_test_was_deleted(tmp_path: Path, monkeypatch) -> None:
    # before: 2 tests, red. agent "fixes" by deleting one. green but fewer tests → refuse.
    (tmp_path / "test_a.py").write_text("def test_one():\n    assert True\ndef test_two():\n    assert True\n")
    seq = iter([_verdict(False), _verdict(True)])

    def fake_verify(root, timeout=600):
        return next(seq)

    def cheating_agent(prompt):
        # delete a test to make the suite "pass"
        (tmp_path / "test_a.py").write_text("def test_one():\n    assert True\n")

    monkeypatch.setattr(repair, "run_verify", fake_verify)
    monkeypatch.setattr(repair, "_git_diff", lambda root: "diff")
    v = repair.repair_to_green(tmp_path, cheating_agent, rounds=1)
    assert v.passed is False                       # green was rejected
    assert "test count dropped" in v.summary


def test_legit_green_with_same_tests_is_accepted(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "test_a.py").write_text("def test_one():\n    assert True\n")
    seq = iter([_verdict(False), _verdict(True)])
    monkeypatch.setattr(repair, "run_verify", lambda root, timeout=600: next(seq))
    monkeypatch.setattr(repair, "_git_diff", lambda root: "diff")
    v = repair.repair_to_green(tmp_path, lambda p: None, rounds=1)  # agent doesn't touch tests
    assert v.passed is True


def test_loop_breaks_when_repair_breaks_detection(tmp_path: Path, monkeypatch) -> None:
    # red → agent breaks the suite (ran=False) → loop stops instead of hammering
    seq = iter([_verdict(False), _verdict(False, ran=False), _verdict(False)])
    monkeypatch.setattr(repair, "run_verify", lambda root, timeout=600: next(seq))
    monkeypatch.setattr(repair, "_git_diff", lambda root: "diff")
    calls: list = []
    v = repair.repair_to_green(tmp_path, lambda p: calls.append(p), rounds=3)
    assert not v.ran
    assert len(calls) == 1            # stopped after the ran=False, didn't use all 3 rounds
