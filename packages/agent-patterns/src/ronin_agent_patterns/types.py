from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

# Sentinel key: when a provider cannot parse a tool call's JSON arguments, it
# stows the raw string under this key instead of silently coercing to {}. The
# executor detects it and returns a "malformed arguments" error so the model
# fixes its JSON, rather than being mis-coached about argument names.
MALFORMED_ARGS_KEY = "__ronin_malformed_args__"


class Tool(BaseModel):
    """A tool the agent can invoke. Wraps a Python callable with a JSON-schema input contract."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    # Marks a tool as side-effecting / not auto-approvable — the host's approval
    # gate should prompt before running it (e.g. an MCP write, a user plugin).
    # Built-in file/shell tools are gated by name on the host side, so they leave
    # this False; it's the signal for tools the host can't enumerate ahead of time.
    sensitive: bool = False

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


StepKind = Literal[
    "thought",
    "tool_call",
    "tool_result",
    "reflection",
    "plan",
    "final",
    "error",
]


class Step(BaseModel):
    """One step in an agent's execution trace. The trace is the agent's audit log."""

    kind: StepKind
    content: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    """Final result of an agent run."""

    success: bool
    output: str = ""
    iterations: int = 0
    trace: list[Step] = Field(default_factory=list)
    error: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    # The full structured conversation after the run — the seeded ``history`` (if
    # any), this turn's user message, every assistant/tool exchange, and the final
    # assistant reply. Callers persist this and feed it back as ``history`` on the
    # next turn so the agent keeps real context (not a flattened text tail).
    # Typed ``Any`` to avoid a types→providers import cycle; items are ``Message``.
    messages: list[Any] = Field(default_factory=list)
