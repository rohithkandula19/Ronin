"""Provider-specific tests: mock httpx for OpenAICompatProvider and the anthropic SDK for AnthropicProvider."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from ro_claude_kit_agent_patterns import (
    AnthropicProvider,
    Message,
    OllamaProvider,
    OpenAICompatProvider,
    Tool,
    ToolCall,
)


# ---------- AnthropicProvider ----------


def _anthropic_response(text="ok", tool_use=None):
    blocks = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    if tool_use:
        blocks.append(SimpleNamespace(type="tool_use", id=tool_use["id"], name=tool_use["name"], input=tool_use["input"]))
    return SimpleNamespace(
        content=blocks,
        stop_reason="tool_use" if tool_use else "end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def test_anthropic_provider_text_response() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response("hello")
    provider = AnthropicProvider(api_key="sk-test")
    with patch("ro_claude_kit_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        response = provider.complete(
            system="be nice",
            messages=[Message(role="user", content="hi")],
            tools=[],
        )
    assert response.text == "hello"
    assert response.tool_calls == []
    assert response.stop_reason == "end_turn"
    assert response.usage["input_tokens"] == 10


def test_anthropic_provider_tool_call() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response(
        text="",
        tool_use={"id": "t1", "name": "calc", "input": {"x": 1}},
    )
    provider = AnthropicProvider(api_key="sk-test")
    with patch("ro_claude_kit_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        response = provider.complete(system="x", messages=[Message(role="user", content="?")], tools=[])

    assert response.tool_calls[0].name == "calc"
    assert response.tool_calls[0].arguments == {"x": 1}
    assert response.stop_reason == "tool_use"


def test_anthropic_provider_batches_consecutive_tool_messages() -> None:
    """All tool_result blocks for one assistant turn should be one user message in Anthropic's format."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response("done")
    provider = AnthropicProvider(api_key="sk-test")
    messages = [
        Message(role="user", content="q"),
        Message(role="assistant", content="", tool_calls=[
            ToolCall(id="t1", name="a", arguments={}),
            ToolCall(id="t2", name="b", arguments={}),
        ]),
        Message(role="tool", tool_call_id="t1", content="r1"),
        Message(role="tool", tool_call_id="t2", content="r2"),
    ]
    with patch("ro_claude_kit_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        provider.complete(system="x", messages=messages, tools=[])

    sent = fake_client.messages.create.call_args.kwargs["messages"]
    # Should be: user, assistant (with two tool_use blocks), user (with two tool_result blocks)
    assert sent[0]["role"] == "user"
    assert sent[1]["role"] == "assistant"
    assert len([b for b in sent[1]["content"] if b["type"] == "tool_use"]) == 2
    assert sent[2]["role"] == "user"
    tool_result_blocks = [b for b in sent[2]["content"] if b["type"] == "tool_result"]
    assert len(tool_result_blocks) == 2


# ---------- OpenAICompatProvider ----------


def _openai_response(content="hello", tool_calls=None, status=200):
    body = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls or [],
            },
            "finish_reason": "tool_calls" if tool_calls else "stop",
        }],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = body
    response.raise_for_status = MagicMock()
    return response


def test_openai_compat_text() -> None:
    response = _openai_response(content="hi")
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = response

    with patch("ro_claude_kit_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="gpt-4o-mini", api_key="sk-x")
        result = provider.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])

    assert result.text == "hi"
    assert result.stop_reason == "end_turn"
    assert result.usage["input_tokens"] == 12

    # Verify auth header was set
    headers = fake_client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer sk-x"


def test_openai_compat_tool_call_parses_json_arguments() -> None:
    response = _openai_response(
        content="",
        tool_calls=[{
            "id": "tc1",
            "type": "function",
            "function": {"name": "calc", "arguments": '{"expression": "2+2"}'},
        }],
    )
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = response

    with patch("ro_claude_kit_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="gpt-4o-mini", api_key="sk-x")
        result = provider.complete(system="s", messages=[Message(role="user", content="?")], tools=[])

    assert result.tool_calls[0].name == "calc"
    assert result.tool_calls[0].arguments == {"expression": "2+2"}
    assert result.stop_reason == "tool_use"


def test_openai_compat_tools_serialized_correctly() -> None:
    response = _openai_response(content="ok")
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = response

    tool = Tool(
        name="search",
        description="search the kb",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        handler=lambda query: query,
    )
    with patch("ro_claude_kit_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="gpt-4o-mini", api_key="sk-x")
        provider.complete(system="s", messages=[Message(role="user", content="?")], tools=[tool])

    body = fake_client.post.call_args.kwargs["json"]
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "search"
    assert body["tools"][0]["function"]["parameters"]["properties"]["query"]["type"] == "string"


def test_ollama_provider_defaults_to_localhost() -> None:
    provider = OllamaProvider(model="llama3.1")
    assert provider.base_url.startswith("http://localhost:11434")


def test_openai_compat_translates_tool_messages() -> None:
    response = _openai_response(content="done")
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = response

    messages = [
        Message(role="user", content="q"),
        Message(role="assistant", content="thinking", tool_calls=[ToolCall(id="t1", name="search", arguments={"q": "x"})]),
        Message(role="tool", tool_call_id="t1", content="result", name="search"),
    ]
    with patch("ro_claude_kit_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="gpt-4o-mini", api_key="sk-x")
        provider.complete(system="s", messages=messages, tools=[])

    sent = fake_client.post.call_args.kwargs["json"]["messages"]
    # system + user + assistant (with tool_calls) + tool
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[2]["tool_calls"][0]["function"]["name"] == "search"
    assert sent[3]["tool_call_id"] == "t1"


def test_openai_compat_retries_on_429(monkeypatch) -> None:
    """A transient 429 is retried (with backoff) instead of failing the turn."""
    import ro_claude_kit_agent_patterns.providers.openai_compat as oc
    monkeypatch.setattr(oc.time, "sleep", lambda *_a, **_k: None)  # no real waiting

    r429 = MagicMock(spec=httpx.Response)
    r429.status_code = 429
    r429.headers = {"retry-after": "0"}
    ok = _openai_response(content="recovered")

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.side_effect = [r429, r429, ok]  # two limits, then success

    with patch("ro_claude_kit_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="m", api_key="sk-x")
        result = provider.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])

    assert result.text == "recovered"
    assert fake_client.post.call_count == 3
