"""Provider-neutral message/tool-call types used by every agent pattern."""
from __future__ import annotations

from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..types import Tool


class ToolCall(BaseModel):
    """A model-emitted call to a tool."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Opaque provider-specific data that must be echoed back on follow-up turns
    # (e.g. Gemini's ``extra_content`` carrying a ``thought_signature``).
    provider_meta: dict[str, Any] | None = None


class Message(BaseModel):
    """Provider-neutral conversation turn.

    Roles:
    - ``user`` — input from the user.
    - ``assistant`` — model output (text + optional tool_calls).
    - ``tool`` — tool execution result, addressed to a specific ``tool_call_id``.
    """

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # tool name (only for role=tool)
    is_error: bool = False


class LLMResponse(BaseModel):
    """One response from a provider."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use" | "max_tokens" | "other"
    usage: dict[str, int] = Field(default_factory=dict)


class StreamEvent(BaseModel):
    """One event from a provider's token stream.

    - ``type="text"``  → ``text`` carries a text delta (a chunk of the answer).
    - ``type="done"``  → ``response`` carries the fully-assembled ``LLMResponse``
      (text + tool_calls + usage), which the agent loop uses to continue.
    """

    type: Literal["text", "done"]
    text: str = ""
    response: LLMResponse | None = None


class LLMProvider(BaseModel):
    """Base class. Subclass and implement ``complete``.

    The agent loop calls ``complete`` once per iteration, passing the full
    conversation so far. The provider is responsible for translating the neutral
    ``Message`` list to the provider's native wire format.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        raise NotImplementedError

    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> Iterator[StreamEvent]:
        """Yield text deltas as they arrive, then a final ``done`` event.

        Default implementation wraps ``complete`` (no real streaming) so every
        provider works through the streaming code path. Providers that support
        native streaming (Anthropic, OpenAI-compatible) override this to emit
        tokens as they're generated.
        """
        resp = self.complete(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        if resp.text:
            yield StreamEvent(type="text", text=resp.text)
        yield StreamEvent(type="done", response=resp)
