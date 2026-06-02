from __future__ import annotations

import uuid
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field


class ToolAllowlist(BaseModel):
    """Enforce that the agent can only call tools from a registered set.

    Wrap your tool-resolution logic with ``check`` before dispatching the call.
    A LLM that hallucinates a tool name (or an attacker who rewrites the input
    to suggest one) will be blocked here, not at the API boundary.
    """

    allowed: set[str]

    def check(self, tool_name: str) -> bool:
        return tool_name in self.allowed

    def assert_allowed(self, tool_name: str) -> None:
        if not self.check(tool_name):
            raise PermissionError(f"tool '{tool_name}' is not on the allowlist")


class PendingApproval(BaseModel):
    """A tool call awaiting human approval."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    args: dict[str, Any]
    reason: str = ""


class ApprovalGate(BaseModel):
    """Human-in-the-loop gate for high-stakes tools (writes, money, deletions).

    When the agent invokes a gated tool, ``request`` returns a ``PendingApproval``
    instead of running the tool. Your application surfaces it to a human; on
    approval, call ``execute`` to run the underlying handler.

    The gate keeps a small in-memory queue of pending calls. For production,
    persist them (DB, queue) and wire ``request`` / ``execute`` through your
    review UI.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    handlers: dict[str, Callable[..., Any]] = Field(default_factory=dict)
    pending: dict[str, PendingApproval] = Field(default_factory=dict)
    auto_approve: bool = False

    def register(self, tool_name: str, handler: Callable[..., Any]) -> None:
        self.handlers[tool_name] = handler

    def request(self, tool_name: str, args: dict[str, Any], reason: str = "") -> PendingApproval | Any:
        if tool_name not in self.handlers:
            raise KeyError(f"no handler registered for '{tool_name}'")
        if self.auto_approve:
            return self.handlers[tool_name](**args)
        approval = PendingApproval(tool_name=tool_name, args=args, reason=reason)
        self.pending[approval.id] = approval
        return approval

    def execute(self, approval_id: str) -> Any:
        approval = self.pending.pop(approval_id, None)
        if approval is None:
            raise KeyError(f"no pending approval with id={approval_id}")
        handler = self.handlers[approval.tool_name]
        return handler(**approval.args)

    def reject(self, approval_id: str) -> bool:
        return self.pending.pop(approval_id, None) is not None
