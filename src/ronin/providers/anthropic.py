"""Anthropic adapter: native tools, ``cache_control``, thinking blocks.

The three things this adapter knows that no other one does:

* **Tool arguments stream as ``input_json_delta``**, not as an ``arguments``
  string on a function object. The fragments still only make sense concatenated,
  so they go straight into the shared accumulator.
* **``cache_control`` is a per-block marker**, not a request-level flag. It marks
  the *end* of the stable prefix, and it has to land on the last stable block or
  the cache covers less than it could. :mod:`ronin.providers.assembly` decides
  where; this adapter only places it.
* **Thinking blocks carry a signature** that must be replayed verbatim on the
  next turn or the continuation is rejected. It travels on
  ``ronin.core.types.Thinking.signature`` and is never edited.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ..core.types import ContentBlock, Message, Role, Text, Thinking, ToolResultBlock, ToolSpec
from .base import ModelClient, Transport, join_url, sse_json
from .normalize import ToolCallAccumulator
from .types import (
    Capabilities,
    Completed,
    FinishReason,
    ModelDelta,
    ModelRequest,
    TextDelta,
    ThinkingDelta,
    Usage,
)

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"

#: Anthropic's stop reasons → ours. Anything unlisted becomes ``UNKNOWN`` on
#: purpose, so a new stop reason shows up as unfamiliar rather than as "stop".
_FINISH = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "tool_use": FinishReason.TOOL_USE,
    "max_tokens": FinishReason.MAX_TOKENS,
    "refusal": FinishReason.CONTENT_FILTER,
}


class AnthropicClient:
    """Talks the Anthropic Messages API.

    Capabilities are declared per-model by the caller rather than sniffed, because
    sniffing means a network round trip before the first token and a wrong guess
    when the endpoint is a proxy.
    """

    def __init__(
        self,
        *,
        model: str,
        transport: Transport,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        capabilities: Capabilities | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._model = model
        self._transport = transport
        self._api_key = api_key
        self._base_url = base_url
        self._capabilities = capabilities or Capabilities(
            native_tools=True,
            parallel_tools=True,
            prompt_cache=True,
            thinking=True,
            max_context=200_000,
            vision=True,
        )
        self._extra_headers = dict(extra_headers or {})

    def capabilities(self) -> Capabilities:
        return self._capabilities

    # ------------------------------------------------------------------ #
    # Request building
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "anthropic-version": API_VERSION,
            **self._extra_headers,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def build_body(self, req: ModelRequest) -> dict[str, Any]:
        """The JSON body for ``req``. Public so tests can assert on it directly."""
        body: dict[str, Any] = {
            "model": req.model or self._model,
            "max_tokens": req.max_tokens,
            "stream": True,
            "messages": [_render_message(m) for m in req.messages],
        }
        system_blocks = _render_system(req, cacheable=self._capabilities.prompt_cache)
        if system_blocks:
            body["system"] = system_blocks
        if req.tools:
            body["tools"] = _render_tools(req.tools, cache_last=self._should_cache_tools(req))
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.thinking_budget and self._capabilities.thinking:
            body["thinking"] = {"type": "enabled", "budget_tokens": req.thinking_budget}
        return body

    def _should_cache_tools(self, req: ModelRequest) -> bool:
        """Whether the cache marker belongs on the last tool rather than the system.

        Tool definitions sit *after* the system prompt in Anthropic's prefix, so
        when both are present the marker goes on the last tool — marking the
        system prompt instead would leave the tool schemas outside the cached span
        and re-bill them every turn.
        """
        return bool(req.tools) and self._capabilities.prompt_cache and req.cache_marker >= 0

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        """Stream one request, yielding text, thinking, and one ``Completed``."""
        url = join_url(self._base_url, "/v1/messages")
        body = self.build_body(req)
        raw = self._transport.send(url=url, headers=self._headers(), body=body)

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_signature = ""
        accumulator = ToolCallAccumulator()
        finish = FinishReason.UNKNOWN
        usage = Usage()
        # Anthropic indexes *content blocks*, and tool calls are a subset of them.
        # Passing the content index straight to the accumulator would leave gaps;
        # this maps content index → tool-call ordinal.
        tool_index: dict[int, int] = {}

        async for event in sse_json(raw):
            kind = event.get("type")
            if kind == "message_start":
                usage = _usage_from(event.get("message", {}).get("usage", {}))
            elif kind == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    index = int(event.get("index", 0))
                    ordinal = len(tool_index)
                    tool_index[index] = ordinal
                    accumulator.add(
                        ordinal,
                        call_id=_as_str(block.get("id")),
                        name=_as_str(block.get("name")),
                    )
            elif kind == "content_block_delta":
                delta = event.get("delta", {})
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    piece = _as_str(delta.get("text")) or ""
                    text_parts.append(piece)
                    if piece:
                        yield TextDelta(piece)
                elif delta_type == "thinking_delta":
                    piece = _as_str(delta.get("thinking")) or ""
                    thinking_parts.append(piece)
                    if piece:
                        yield ThinkingDelta(piece)
                elif delta_type == "signature_delta":
                    thinking_signature += _as_str(delta.get("signature")) or ""
                elif delta_type == "input_json_delta":
                    index = int(event.get("index", 0))
                    known = tool_index.get(index)
                    if known is not None:
                        accumulator.add(
                            known,
                            arguments_fragment=_as_str(delta.get("partial_json")) or "",
                        )
            elif kind == "message_delta":
                stop = _as_str(event.get("delta", {}).get("stop_reason"))
                if stop:
                    finish = _FINISH.get(stop, FinishReason.UNKNOWN)
                # `message_delta` reports the *running total* of output tokens, not
                # an increment. Adding it to the `message_start` value would bill
                # the first token twice on every request — small per call, and
                # wrong in the direction that makes a budget stop early.
                usage = _merge_usage(usage, event.get("usage", {}))
            elif kind == "error":
                detail = _as_str(event.get("error", {}).get("message")) or "provider error"
                yield Completed(
                    message=_assemble(text_parts, thinking_parts, thinking_signature, None),
                    finish=FinishReason.ERROR,
                    usage=usage,
                    notes=(f"anthropic stream error: {detail}",),
                )
                return

        normalized = accumulator.finish()
        if normalized.uses:
            finish = FinishReason.TOOL_USE
        yield Completed(
            message=_assemble(text_parts, thinking_parts, thinking_signature, normalized.uses),
            finish=finish if finish is not FinishReason.UNKNOWN else FinishReason.STOP,
            usage=usage,
            notes=normalized.notes + tuple(f"unparseable: {f.error}" for f in normalized.failed),
        )


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #


def _render_system(req: ModelRequest, *, cacheable: bool) -> list[dict[str, Any]]:
    """The system prompt as blocks, with the cache marker on the last one.

    Blocks rather than a bare string because ``cache_control`` is per-block: a
    string cannot be marked, so a string cannot be cached.
    """
    blocks: list[dict[str, Any]] = []
    if req.system:
        blocks.append({"type": "text", "text": req.system})
    if req.prefix:
        blocks.append({"type": "text", "text": req.prefix})
    if blocks and cacheable and req.cache_marker >= 0 and not req.tools:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def _render_tools(tools: Sequence[ToolSpec], *, cache_last: bool) -> list[dict[str, Any]]:
    rendered = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": dict(spec.json_schema) or {"type": "object", "properties": {}},
        }
        for spec in tools
    ]
    if rendered and cache_last:
        rendered[-1]["cache_control"] = {"type": "ephemeral"}
    return rendered


def _render_message(message: Message) -> dict[str, Any]:
    """One transcript message in Anthropic's block shape.

    A system-role message inside ``messages`` is rendered as a user turn: the API
    has no system role in the array, and dropping it would silently lose an
    instruction (the shim's nudges arrive this way).
    """
    role = "assistant" if message.role is Role.ASSISTANT else "user"
    blocks: list[dict[str, Any]] = []
    for block in message.content_blocks:
        if isinstance(block, Text):
            if block.text:
                blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, Thinking):
            entry: dict[str, Any] = {"type": "thinking", "thinking": block.text}
            if block.signature:
                entry["signature"] = block.signature
            blocks.append(entry)
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
        else:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": dict(block.arguments),
                }
            )
    return {"role": role, "content": blocks}


def _assemble(
    text_parts: Sequence[str],
    thinking_parts: Sequence[str],
    signature: str,
    uses: Sequence[ContentBlock] | None,
) -> Message:
    blocks: list[ContentBlock] = []
    thinking = "".join(thinking_parts)
    if thinking:
        blocks.append(Thinking(thinking, signature=signature))
    text = "".join(text_parts)
    if text:
        blocks.append(Text(text))
    if uses:
        blocks.extend(uses)
    return Message(role=Role.ASSISTANT, content_blocks=tuple(blocks))


def _merge_usage(current: Usage, raw: Mapping[str, Any]) -> Usage:
    """Fold a ``message_delta`` usage report into the running total.

    Every field here *replaces* rather than accumulates, because Anthropic reports
    cumulative counts on the final event. Absent fields keep the value we already
    had, which is how the input count from ``message_start`` survives.
    """
    incoming = _usage_from(raw)
    return Usage(
        input_tokens=incoming.input_tokens or current.input_tokens,
        output_tokens=incoming.output_tokens or current.output_tokens,
        cache_read_tokens=incoming.cache_read_tokens or current.cache_read_tokens,
        cache_write_tokens=incoming.cache_write_tokens or current.cache_write_tokens,
    )


def _usage_from(raw: Mapping[str, Any]) -> Usage:
    return Usage(
        input_tokens=_as_int(raw.get("input_tokens")),
        output_tokens=_as_int(raw.get("output_tokens")),
        cache_read_tokens=_as_int(raw.get("cache_read_input_tokens")),
        cache_write_tokens=_as_int(raw.get("cache_creation_input_tokens")),
    )


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _conforms(client: AnthropicClient) -> ModelClient:
    """Static proof that this adapter satisfies the protocol.

    ``runtime_checkable`` only checks that the methods *exist*; this makes mypy
    check their signatures, so an adapter that drifts from the contract fails the
    type gate instead of failing at the first call.
    """
    return client
