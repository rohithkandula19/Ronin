from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class Tool(BaseModel):
    """A tool the agent can invoke. Wraps a Python callable with a JSON-schema input contract."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]

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
