"""Anthropic Claude provider — the default."""
from __future__ import annotations

from typing import Any


from typing import Iterator

from ..types import Tool
from .base import LLMProvider, LLMResponse, Message, StreamEvent, ToolCall


def __getattr__(name: str):
    """Lazily expose the ``anthropic`` SDK as a module attribute.

    The SDK is a ~0.7s import; keeping it off the module's top-level means
    ``ronin --help`` (which imports this module transitively) never pays for it.
    Accessing ``anthropic_provider.anthropic`` — including via test patches like
    ``patch('...anthropic_provider.anthropic.Anthropic')`` — imports it on first
    use and returns the real module, so patching still works.
    """
    if name == "anthropic":
        import anthropic
        return anthropic
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


# Transient statuses worth retrying — overload (529), rate limit (429) and 5xx
# are routine at peak; an agent loop fires several calls per task, so retrying
# saves the whole turn. Mirrors the OpenAI-compat path so the paid Anthropic
# path is no longer the least-resilient provider in the stack.
_RETRY_STATUSES = {429, 500, 502, 503, 529}
_MAX_RETRIES = 6


def _retry_wait(attempt: int, retry_after: str | None) -> float:
    """Seconds before retry ``attempt`` (0-based): honour a numeric Retry-After,
    else exponential backoff (2,4,8,16,30,30s)."""
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            pass
    return float(min(2 ** (attempt + 1), 30))


def _status_of(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def _retry_after_of(exc: Exception) -> str | None:
    resp = getattr(exc, "response", None)
    try:
        return resp.headers.get("retry-after") if resp is not None else None
    except Exception:  # noqa: BLE001
        return None


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
    # Transient-error retries (429/5xx/529 + connection drops), matching the
    # OpenAI-compat path. Set 0 to disable.
    max_retries: int = _MAX_RETRIES

    def _client(self) -> "anthropic.Anthropic":
        # Lazy import: keep the ~0.7s anthropic SDK load off the `ronin --help`
        # path; it only happens when a real client is constructed.
        import anthropic
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
        import time

        import anthropic
        kwargs = self._build_kwargs(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        client = self._client()
        for attempt in range(self.max_retries + 1):
            try:
                response = client.messages.create(**kwargs)
                return _parse_anthropic_message(response)
            except anthropic.APIStatusError as e:
                if _status_of(e) in _RETRY_STATUSES and attempt < self.max_retries:
                    time.sleep(_retry_wait(attempt, _retry_after_of(e)))
                    continue
                raise
            except anthropic.APIConnectionError:
                if attempt < self.max_retries:
                    time.sleep(_retry_wait(attempt, None))
                    continue
                raise
        raise RuntimeError("unreachable: retry loop exhausted without return/raise")

    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> Iterator[StreamEvent]:
        import time

        import anthropic
        kwargs = self._build_kwargs(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        client = self._client()
        # Each attempt re-streams from scratch. If a prior attempt already
        # forwarded text, emit a ``reset`` first so the renderer discards the
        # partial and the answer renders exactly once (the LiveRenderer.on_reset
        # contract, shared with the OpenAI-compat path).
        for attempt in range(self.max_retries + 1):
            emitted = False
            try:
                with client.messages.stream(**kwargs) as stream:
                    for delta in stream.text_stream:
                        if delta:
                            emitted = True
                            yield StreamEvent(type="text", text=delta)
                    final = stream.get_final_message()
                yield StreamEvent(type="done", response=_parse_anthropic_message(final))
                return
            except anthropic.APIStatusError as e:
                if _status_of(e) in _RETRY_STATUSES and attempt < self.max_retries:
                    if emitted:
                        yield StreamEvent(type="reset")
                    time.sleep(_retry_wait(attempt, _retry_after_of(e)))
                    continue
                raise
            except anthropic.APIConnectionError:
                # Connection dropped mid-stream (routine on flaky links); reset the
                # partial and re-attempt the whole request.
                if attempt < self.max_retries:
                    if emitted:
                        yield StreamEvent(type="reset")
                    time.sleep(_retry_wait(attempt, None))
                    continue
                raise
