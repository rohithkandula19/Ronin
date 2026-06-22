"""Anthropic Claude provider — the default."""
from __future__ import annotations

from typing import Any

import anthropic

from typing import Iterator

from ..types import Tool
from .base import LLMProvider, LLMResponse, Message, StreamEvent, ToolCall


def _parse_anthropic_message(message: Any) -> LLMResponse:
    """Turn an Anthropic Message (from create() or get_final_message()) into our
    neutral ``LLMResponse``."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in message.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(block.text)
        elif block_type == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input or {}))
    usage: dict[str, int] = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }
    # Prompt-cache accounting (present when cache_control breakpoints are set).
    # ``cache_read_input_tokens`` are billed at ~10% of the normal input rate;
    # ``cache_creation_input_tokens`` is the one-time cost of writing the cache.
    for attr in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        val = getattr(message.usage, attr, None)
        if val is not None:
            usage[attr] = val
    return LLMResponse(
        text="\n".join(text_parts).strip(),
        tool_calls=tool_calls,
        stop_reason="tool_use" if tool_calls else "end_turn",
        usage=usage,
    )


def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Translate neutral messages to Anthropic's wire format.

    Anthropic batches all tool_result blocks for one assistant turn into a single
    user message. So we walk the neutral list and merge consecutive ``tool``
    messages into one user message with multiple ``tool_result`` blocks.
    """
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.role == "tool":
            blocks: list[dict[str, Any]] = []
            while i < len(messages) and messages[i].role == "tool":
                tm = messages[i]
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tm.tool_call_id,
                    "content": tm.content,
                    "is_error": tm.is_error,
                })
                i += 1
            out.append({"role": "user", "content": blocks})
            continue

        if m.role == "assistant":
            content_blocks: list[dict[str, Any]] = []
            if m.content:
                content_blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                })
            out.append({
                "role": "assistant",
                "content": content_blocks if content_blocks else m.content,
            })
        else:  # user
            out.append({"role": "user", "content": m.content})
        i += 1
    return out


_EPHEMERAL = {"type": "ephemeral"}


def _mark_cached(msg: dict) -> None:
    """Put a cache_control breakpoint on a message's last content block (so the
    whole conversation up to and including it is cached). String content is
    promoted to a single text block first."""
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = [{"type": "text", "text": content, "cache_control": _EPHEMERAL}]
    elif isinstance(content, list) and content:
        content[-1] = {**content[-1], "cache_control": _EPHEMERAL}


class AnthropicProvider(LLMProvider):
    """Calls the Anthropic Messages API."""

    model: str = "claude-sonnet-4-6"
    api_key: str | None = None
    # Prompt caching is on by default — it's a pure win (same output, up to ~90%
    # cheaper + faster on the cached prefix). Set False to opt out.
    cache_prompt: bool = True
    # Reasoning budget ("low"|"medium"|"high"|"xhigh"). None → extended thinking
    # is left off and the request body is byte-for-byte unchanged (the default).
    effort: str | None = None

    def _client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()

    def _build_kwargs(
        self, *, system: str, messages: list[Message], tools: list[Tool], max_tokens: int
    ) -> dict[str, Any]:
        """Assemble the create()/stream() kwargs, applying cache breakpoints.

        Breakpoints go on the large, stable prefixes so each turn reads them from
        cache: the tool schemas, the system prompt, AND the conversation prefix.
        Anthropic caches everything *before* a breakpoint and caches incrementally,
        so marking the last message caches the whole prior conversation — a long
        agent loop then re-reads its history at ~10% cost instead of full price,
        and only the newest delta is written. (Below the ~1k-token minimum the
        breakpoint is simply ignored, so short turns cost nothing.)"""
        msgs = _to_anthropic_messages(messages)
        # Cache the conversation prefix (only worth a breakpoint past the first
        # exchange — a single short user turn won't reach the cache minimum).
        if self.cache_prompt and len(msgs) >= 2:
            _mark_cached(msgs[-1])
        kwargs: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": msgs,
            "max_tokens": max_tokens,
        }
        if tools:
            tool_defs = [t.to_anthropic() for t in tools]
            if self.cache_prompt:
                tool_defs[-1] = {**tool_defs[-1], "cache_control": _EPHEMERAL}
            kwargs["tools"] = tool_defs
        if self.cache_prompt and system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": _EPHEMERAL}
            ]
        # Reasoning budget: merges a ``thinking`` block when an effort level is set
        # (medium/high/xhigh). None or "low" → no merge, so the default request is
        # unchanged. Covers both complete() and stream() since both route here.
        if self.effort:
            from ..effort import effort_to_params
            kwargs.update(effort_to_params("anthropic", self.effort))
        return kwargs

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        response = self._client().messages.create(**kwargs)
        return _parse_anthropic_message(response)

    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> Iterator[StreamEvent]:
        kwargs = self._build_kwargs(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        with self._client().messages.stream(**kwargs) as stream:
            for delta in stream.text_stream:
                if delta:
                    yield StreamEvent(type="text", text=delta)
            final = stream.get_final_message()
        yield StreamEvent(type="done", response=_parse_anthropic_message(final))
