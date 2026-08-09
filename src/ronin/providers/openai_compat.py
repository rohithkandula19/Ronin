"""One adapter for every ``/chat/completions`` server.

Covers OpenAI, DeepSeek, Together, Groq, OpenRouter, vLLM, llama.cpp's server,
LM Studio and Ollama. They differ in ``base_url``, in which optional fields they
send back, and in how badly they mangle tool calls — none of which is a reason for
eight adapters. What *would* justify a separate adapter is a different wire
protocol, which is why Anthropic and MLX have their own.

The quirks this handles generically, because more than one backend has each:

* ``tool_calls[].function.arguments`` arrives as string fragments across chunks.
* ``index`` is sometimes absent on later fragments of the same call; we fall back
  to the last index we saw rather than starting a new call.
* ``usage`` is absent unless you ask for it, and several backends ignore the ask.
  Absent means "not reported", never zero — see :mod:`ronin.providers.accounting`.
* Some backends send the whole message in one non-streaming-shaped chunk even
  though ``stream=true`` was set; a ``message`` key is handled alongside ``delta``.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
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

#: Base URLs that come up often enough to be worth not looking up. Config may
#: name any of these or pass a URL directly — the adapter does not care which.
KNOWN_BASE_URLS: Mapping[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "together": "https://api.together.xyz/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080/v1",
}

_FINISH = {
    "stop": FinishReason.STOP,
    "tool_calls": FinishReason.TOOL_USE,
    "function_call": FinishReason.TOOL_USE,
    "length": FinishReason.MAX_TOKENS,
    "content_filter": FinishReason.CONTENT_FILTER,
}


class OpenAICompatClient:
    """Talks the OpenAI chat-completions API to whatever is at ``base_url``."""

    #: Subclasses override to adjust one provider's quirks without a fork.
    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        model: str,
        transport: Transport,
        base_url: str,
        api_key: str = "",
        capabilities: Capabilities | None = None,
        extra_headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._transport = transport
        self._base_url = KNOWN_BASE_URLS.get(base_url, base_url)
        self._api_key = api_key
        self._capabilities = capabilities or Capabilities(
            native_tools=True,
            parallel_tools=True,
            prompt_cache=False,
            thinking=False,
            max_context=128_000,
            vision=False,
        )
        self._extra_headers = dict(extra_headers or {})
        self._extra_body = dict(extra_body or {})

    def capabilities(self) -> Capabilities:
        return self._capabilities

    # ------------------------------------------------------------------ #
    # Request building
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            # Some providers sit behind a WAF that 403s the default httpx agent.
            "user-agent": "ronin (+https://github.com/rohithkandula19/Ronin)",
            **self._extra_headers,
        }
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def build_body(self, req: ModelRequest) -> dict[str, Any]:
        """The JSON body for ``req``. Public so tests can assert on it directly."""
        body: dict[str, Any] = {
            "model": req.model or self._model,
            "stream": True,
            "max_tokens": req.max_tokens,
            "messages": list(self._render_messages(req)),
        }
        if req.tools and self._capabilities.native_tools:
            body["tools"] = [_render_tool(spec) for spec in req.tools]
        if req.temperature is not None:
            body["temperature"] = req.temperature
        # Ask for usage in the final chunk. Backends that do not know the option
        # ignore it; none of them reject it.
        body["stream_options"] = {"include_usage": True}
        body.update(self._extra_body)
        return body

    def _render_messages(self, req: ModelRequest) -> Iterable[dict[str, Any]]:
        """Render the transcript, system prompt first.

        The system prompt and the stable prefix are one message rather than two.
        Splitting them would put a turn boundary in the middle of the stable
        prefix, and several backends hash the prefix per message.
        """
        head = "\n\n".join(part for part in (req.system, req.prefix) if part)
        if head:
            yield {"role": "system", "content": head}
        for message in req.messages:
            yield from _render_message(message)

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #

    def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        """Stream one request. Subclasses inherit every quirk fix by construction."""
        return _stream_impl(
            self,
            url=join_url(self._base_url, "/chat/completions"),
            headers=self._headers(),
            body=self.build_body(req),
            transport=self._transport,
        )


class MoonshotClient(OpenAICompatClient):
    """Kimi K2 on Moonshot: OpenAI-shaped, with three quirks worth naming.

    1. **Tool-call ids are unreliable.** They are sometimes absent, sometimes
       reused across calls in one response, and sometimes shaped
       ``functions.read_file:0`` — which is a *name* with an index glued on, not an
       id. Passing that through as an id produces ``tool_result`` blocks the API
       later rejects, so ids that look derived-from-name are dropped and the
       accumulator mints a stable one instead.
    2. **``finish_reason`` disagrees with itself.** A response with tool calls may
       report ``stop``. We trust the presence of calls over the label; a turn with
       calls is a ``TOOL_USE`` turn no matter what the field says.
    3. **The ``arguments`` string is occasionally double-encoded** — a JSON string
       containing JSON. :mod:`ronin.providers.jsonargs` unwraps it and records a
       ``double_encoded`` repair, so it shows up in the notes instead of as a tool
       that silently received a string where it wanted an object.

    Everything else is inherited, which is the point: a quirky provider is a
    subclass with three overrides, not a second adapter.
    """

    provider_name = "moonshot"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", "https://api.moonshot.cn/v1")
        kwargs.setdefault(
            "capabilities",
            Capabilities(
                native_tools=True,
                parallel_tools=True,
                prompt_cache=True,
                thinking=False,
                max_context=131_072,
                vision=False,
            ),
        )
        super().__init__(**kwargs)


def _looks_derived_from_name(call_id: str, name: str | None) -> bool:
    """Whether ``call_id`` is really a tool name with an index glued on.

    ``functions.read_file:0`` is not an identifier — it is the same string for
    every call to that tool, so two parallel reads collide. Recognizing the shape
    is better than accepting it and hitting a 400 two turns later.
    """
    if not call_id:
        return False
    if call_id.startswith("functions."):
        return True
    return bool(name) and call_id in {name, f"{name}:0"}


def _tool_call_id(raw: object, name: str | None) -> str | None:
    call_id = raw if isinstance(raw, str) else None
    if call_id and _looks_derived_from_name(call_id, name):
        return None
    return call_id


async def _stream_impl(
    client: OpenAICompatClient,
    *,
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    transport: Transport,
) -> AsyncIterator[ModelDelta]:
    """Shared streaming body, so the quirk subclass inherits every fix."""
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    accumulator = ToolCallAccumulator()
    finish = FinishReason.UNKNOWN
    usage = Usage()
    last_index = 0
    notes: list[str] = []

    async for event in sse_json(transport.send(url=url, headers=headers, body=body)):
        if "error" in event and not event.get("choices"):
            detail = _error_detail(event["error"])
            yield Completed(
                message=_assemble(text_parts, thinking_parts, ()),
                finish=FinishReason.ERROR,
                usage=usage,
                notes=(f"{client.provider_name} stream error: {detail}",),
            )
            return

        raw_usage = event.get("usage")
        if isinstance(raw_usage, Mapping):
            usage = _usage_from(raw_usage)

        choices = event.get("choices")
        if not isinstance(choices, Sequence) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, Mapping):
            continue

        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            finish = _FINISH.get(reason, FinishReason.UNKNOWN)

        # `delta` for a real stream, `message` for backends that answer a stream
        # request with a whole message.
        payload = choice.get("delta")
        if not isinstance(payload, Mapping):
            payload = choice.get("message")
        if not isinstance(payload, Mapping):
            continue

        content = payload.get("content")
        if isinstance(content, str) and content:
            text_parts.append(content)
            yield TextDelta(content)

        # DeepSeek-R1 and several distills stream reasoning on its own field.
        reasoning = payload.get("reasoning_content")
        if not isinstance(reasoning, str):
            reasoning = payload.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            thinking_parts.append(reasoning)
            yield ThinkingDelta(reasoning)

        raw_calls = payload.get("tool_calls")
        if not isinstance(raw_calls, Sequence):
            continue
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                continue
            index_value = raw_call.get("index")
            if isinstance(index_value, int) and not isinstance(index_value, bool):
                last_index = index_value
            index = last_index
            function = raw_call.get("function")
            function = function if isinstance(function, Mapping) else {}
            name = function.get("name")
            name = name if isinstance(name, str) else None
            arguments = function.get("arguments")
            call_id = _tool_call_id(raw_call.get("id"), name)
            if raw_call.get("id") and call_id is None:
                notes.append(
                    f"discarded provider tool-call id {raw_call['id']!r}: "
                    "it is derived from the tool name and is not unique"
                )
            accumulator.add(
                index,
                call_id=call_id,
                name=name,
                arguments_fragment=arguments if isinstance(arguments, str) else "",
            )

    normalized = accumulator.finish()
    if normalized.uses:
        # Quirk 2: the presence of calls beats a `stop` label.
        finish = FinishReason.TOOL_USE
    elif finish is FinishReason.UNKNOWN:
        finish = FinishReason.STOP
    yield Completed(
        message=_assemble(text_parts, thinking_parts, normalized.uses),
        finish=finish,
        usage=usage,
        notes=(
            *notes,
            *normalized.notes,
            *(f"unparseable: {f.error}" for f in normalized.failed),
        ),
    )


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #


def _render_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": dict(spec.json_schema) or {"type": "object", "properties": {}},
        },
    }


def _render_message(message: Message) -> Iterable[dict[str, Any]]:
    """Render one transcript message, splitting tool results into their own turns.

    The chat-completions format has no block list: a tool result is a whole
    message with ``role="tool"``. One of our messages carrying three results
    therefore becomes three messages, and the pairing has to survive that — which
    is why ``tool_call_id`` is copied verbatim and never regenerated.
    """
    if message.role is Role.TOOL or message.tool_results:
        for block in message.tool_results:
            yield {
                "role": "tool",
                "tool_call_id": block.tool_use_id,
                "content": block.content,
            }
        remaining = [b for b in message.content_blocks if not isinstance(b, ToolResultBlock)]
        if not remaining:
            return

    if message.role is Role.ASSISTANT:
        entry: dict[str, Any] = {"role": "assistant", "content": message.text}
        if message.tool_uses:
            entry["tool_calls"] = [
                {
                    "id": use.id,
                    "type": "function",
                    "function": {
                        "name": use.name,
                        "arguments": _dumps(dict(use.arguments)),
                    },
                }
                for use in message.tool_uses
            ]
        yield entry
        return

    role = "system" if message.role is Role.SYSTEM else "user"
    text = message.text
    if text:
        yield {"role": role, "content": text}


def _dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _assemble(
    text_parts: Sequence[str],
    thinking_parts: Sequence[str],
    uses: Sequence[ContentBlock],
) -> Message:
    blocks: list[ContentBlock] = []
    thinking = "".join(thinking_parts)
    if thinking:
        blocks.append(Thinking(thinking))
    text = "".join(text_parts)
    if text:
        blocks.append(Text(text))
    blocks.extend(uses)
    return Message(role=Role.ASSISTANT, content_blocks=tuple(blocks))


def _usage_from(raw: Mapping[str, Any]) -> Usage:
    details = raw.get("prompt_tokens_details")
    cached = 0
    if isinstance(details, Mapping):
        cached = _as_int(details.get("cached_tokens"))
    prompt = _as_int(raw.get("prompt_tokens"))
    return Usage(
        # Cached reads are reported *inside* prompt_tokens here, unlike Anthropic.
        # Subtracting keeps `input_tokens` meaning "billed at the full rate" for
        # every provider, which is what the ledger's pricing assumes.
        input_tokens=max(prompt - cached, 0),
        output_tokens=_as_int(raw.get("completion_tokens")),
        cache_read_tokens=cached,
    )


def _error_detail(error: object) -> str:
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return str(error)[:500]


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _conforms(client: OpenAICompatClient) -> ModelClient:
    """Static proof that this adapter satisfies the protocol."""
    return client
