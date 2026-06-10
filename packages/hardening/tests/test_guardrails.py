from __future__ import annotations

import pytest

from ronin_hardening import ApprovalGate, ToolAllowlist
from ronin_hardening.guardrails import PendingApproval


def test_allowlist_allows_known_tools() -> None:
    al = ToolAllowlist(allowed={"search", "fetch"})
    assert al.check("search") is True
    al.assert_allowed("search")


def test_allowlist_blocks_unknown_tools() -> None:
    al = ToolAllowlist(allowed={"search"})
    assert al.check("delete_user") is False
    with pytest.raises(PermissionError, match="not on the allowlist"):
        al.assert_allowed("delete_user")


def test_approval_gate_returns_pending() -> None:
    calls: list[dict] = []

    def delete_user(user_id: str) -> str:
        calls.append({"user_id": user_id})
        return f"deleted {user_id}"

    gate = ApprovalGate()
    gate.register("delete_user", delete_user)
    pending = gate.request("delete_user", {"user_id": "alice"}, reason="cleanup")

    assert isinstance(pending, PendingApproval)
    assert pending.tool_name == "delete_user"
    assert pending.args == {"user_id": "alice"}
    assert calls == []  # nothing executed yet


def test_approval_gate_executes_on_approval() -> None:
    gate = ApprovalGate()
    gate.register("write", lambda value: f"wrote {value}")

    pending = gate.request("write", {"value": "hello"})
    assert isinstance(pending, PendingApproval)

    result = gate.execute(pending.id)
    assert result == "wrote hello"
    assert pending.id not in gate.pending


def test_approval_gate_rejects() -> None:
    gate = ApprovalGate()
    gate.register("write", lambda: "done")
    pending = gate.request("write", {})
    assert gate.reject(pending.id) is True
    assert gate.reject(pending.id) is False  # idempotent


def test_approval_gate_auto_approve() -> None:
    """auto_approve mode runs the handler immediately — useful for dev/test."""
    gate = ApprovalGate(auto_approve=True)
    gate.register("noop", lambda: "ran")
    result = gate.request("noop", {})
    assert result == "ran"
