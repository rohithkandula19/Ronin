"""Invariant tests: web actions cannot bypass the real Ronin safety floor.

These import the SAME runtime primitives the CLI uses (via coding_runtime),
so a pass here is evidence the web seam enforces the terminal's guarantees.
"""
from __future__ import annotations

from csk_api.v1.coding_runtime import (
    GateOutcome,
    build_gate_cb,
    classify_tool_call,
)


# ------------------------------------------------- the floor is not waivable

def test_destructive_command_denied_even_when_user_approves():
    d = classify_tool_call(
        "run_command", {"command": "rm -rf /"},
        role="developer", human_decision=True,  # user tried to approve
    )
    assert d.outcome is GateOutcome.deny
    assert d.floored is True


def test_force_push_hits_floor():
    d = classify_tool_call(
        "run_command", {"command": "git push --force origin main"},
        role="developer", human_decision=True,
    )
    assert d.outcome is GateOutcome.deny and d.floored


def test_gate_cb_returns_denial_string_for_floored_call():
    # Even if the approvals map says "approve everything", the floor wins.
    gate = build_gate_cb(role="developer", approvals={"run_command": True})
    result = gate("run_command", {"command": "rm -rf /"})
    assert result is not True  # not allowed
    assert isinstance(result, str) and "floor" in result.lower()


# --------------------------------------------------- read-only roles/tools

def test_read_only_role_cannot_write():
    d = classify_tool_call("write_file", {"path": "a.py", "content": "x"},
                           role="reviewer", human_decision=True)
    assert d.outcome is GateOutcome.deny
    assert "read-only" in d.reason


def test_read_tools_pass_without_approval():
    for tool in ("read_file", "search_files", "repo_map", "glob"):
        d = classify_tool_call(tool, {"path": "."}, role="developer")
        assert d.outcome is GateOutcome.allow


# ------------------------------------------------ sensitive needs approval

def test_write_needs_explicit_approval_then_allows():
    pending = classify_tool_call("write_file", {"path": "a.py", "content": "x"},
                                 role="developer", human_decision=None)
    assert pending.outcome is GateOutcome.needs_approval

    approved = classify_tool_call("write_file", {"path": "a.py", "content": "x"},
                                  role="developer", human_decision=True)
    assert approved.outcome is GateOutcome.allow

    rejected = classify_tool_call("write_file", {"path": "a.py", "content": "x"},
                                  role="developer", human_decision=False)
    assert rejected.outcome is GateOutcome.deny


def test_gate_cb_blocks_unapproved_sensitive_call():
    gate = build_gate_cb(role="developer", approvals={})  # nothing approved yet
    result = gate("write_file", {"path": "a.py", "content": "x"})
    assert result is not True  # blocked until approved
    approved_gate = build_gate_cb(role="developer", approvals={"write_file": True})
    assert approved_gate("write_file", {"path": "a.py", "content": "x"}) is True


def test_uses_real_runtime_primitives():
    """Guard against drift: the gate must import the real floor + sensitive set."""
    from csk_api.v1 import coding_runtime
    from ronin_cli.approvals import is_floored_tool_call as real_floor
    from ronin_cli.code_tools import SENSITIVE_TOOLS as real_sensitive

    assert coding_runtime.is_floored_tool_call is real_floor
    assert coding_runtime.SENSITIVE_TOOLS is real_sensitive
    assert "write_file" in real_sensitive and "run_command" in real_sensitive
