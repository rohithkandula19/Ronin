"""OpenAI-compatible provider.

Works with anything exposing OpenAI's ``/chat/completions`` API:
- OpenAI itself (``base_url="https://api.openai.com/v1"``)
- Ollama (``base_url="http://localhost:11434/v1"``, no API key needed)
- Together (``base_url="https://api.together.xyz/v1"``)
- Groq (``base_url="https://api.groq.com/openai/v1"``)
- Fireworks (``base_url="https://api.fireworks.ai/inference/v1"``)
- vLLM, llama.cpp server, LM Studio (any local OpenAI-compat server)

Uses ``httpx`` directly — no ``openai`` SDK dependency.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator

import httpx

from ..types import Tool
from .base import LLMProvider, LLMResponse, Message, StreamEvent, ToolCall


OPENAI_BASE_URL = "https://api.openai.com/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"


def _to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m.role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content,
            })
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant"}
            if m.content:
                msg["content"] = m.content
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            if "content" not in msg:
                msg["content"] = ""
            out.append(msg)
        else:
            out.append({"role": "user", "content": m.content})
    return out


class OpenAICompatProvider(LLMProvider):
    """Calls any OpenAI-compatible ``/chat/completions`` endpoint."""

    model_config = LLMProvider.model_config

    model: str
    base_url: str = OPENAI_BASE_URL
    api_key: str | None = None
    timeout: float = 120.0
    extra_headers: dict[str, str] = {}

    def _resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        return os.environ.get("OPENAI_API_KEY")

    def _build_body(self, system: str, messages: list[Message], tools: list[Tool],
                    max_tokens: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(system, messages),
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
        return body

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json", **self.extra_headers}
        key = self._resolve_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @staticmethod
    def _parse_args(args_raw: Any) -> dict[str, Any]:
        if isinstance(args_raw, str):
            try:
                return json.loads(args_raw)
            except json.JSONDecodeError:
                return {}
        return args_raw or {}

    @staticmethod
    def _stop_reason(finish: str, tool_calls: list[ToolCall]) -> str:
        if tool_calls:
            return "tool_use"
        return "end_turn" if finish in ("stop", "end_turn") else (finish or "end_turn")

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        body = self._build_body(system, messages, tools, max_tokens)
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self._url(), json=body, headers=self._headers())
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]
        msg = choice["message"]
        text = (msg.get("content") or "").strip()

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                arguments=self._parse_args(tc.get("function", {}).get("arguments", "{}")),
            ))

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=self._stop_reason(choice.get("finish_reason") or "end_turn", tool_calls),
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        )

    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> Iterator[StreamEvent]:
        body = self._build_body(system, messages, tools, max_tokens)
        body["stream"] = True

        text_parts: list[str] = []
        # Tool-call deltas arrive piecemeal across chunks, keyed by index; we
        # accumulate id / name / argument-string fragments and assemble at the end.
        acc: dict[int, dict[str, str]] = {}
        finish = "end_turn"
        usage: dict[str, Any] = {}

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream("POST", self._url(), json=body, headers=self._headers()) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            yield StreamEvent(type="text", text=delta["content"])
                        for tcd in delta.get("tool_calls") or []:
                            idx = tcd.get("index", 0)
                            slot = acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tcd.get("id"):
                                slot["id"] = tcd["id"]
                            fn = tcd.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]

        tool_calls = [
            ToolCall(id=slot["id"], name=slot["name"], arguments=self._parse_args(slot["args"] or "{}"))
            for _, slot in sorted(acc.items())
        ]
        yield StreamEvent(type="done", response=LLMResponse(
            text="".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=self._stop_reason(finish, tool_calls),
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        ))


class OllamaProvider(OpenAICompatProvider):
    """Convenience subclass — runs against a local Ollama server, no API key needed.

    Default base_url is the Ollama default (``http://localhost:11434/v1``). Override
    with ``OLLAMA_BASE_URL`` env var or by passing ``base_url=...``.
    """

    base_url: str = os.environ.get("OLLAMA_BASE_URL") or OLLAMA_BASE_URL
    api_key: str | None = "ollama"  # ollama ignores auth but openai-format requires *something*
