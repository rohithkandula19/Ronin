"""Provider-specific tests: mock httpx for OpenAICompatProvider and the anthropic SDK for AnthropicProvider."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from ronin_agent_patterns import (
    AnthropicProvider,
    Message,
    OllamaProvider,
    OpenAICompatProvider,
    Tool,
    ToolCall,
)


# ---------- AnthropicProvider ----------


def _anthropic_response(text="ok", tool_use=None, usage=None):
    blocks = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    if tool_use:
        blocks.append(SimpleNamespace(type="tool_use", id=tool_use["id"], name=tool_use["name"], input=tool_use["input"]))
    return SimpleNamespace(
        content=blocks,
        stop_reason="tool_use" if tool_use else "end_turn",
        usage=usage or SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def _cache_tool() -> Tool:
    return Tool(
        name="calc",
        description="do math",
        input_schema={"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]},
        handler=lambda x: x,
    )


def test_anthropic_provider_text_response() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response("hello")
    provider = AnthropicProvider(api_key="sk-test")
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        response = provider.complete(
            system="be nice",
            messages=[Message(role="user", content="hi")],
            tools=[],
        )
    assert response.text == "hello"
    assert response.tool_calls == []
    assert response.stop_reason == "end_turn"
    assert response.usage["input_tokens"] == 10


def test_anthropic_provider_uses_native_token_counter() -> None:
    fake_client = MagicMock()
    fake_client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=123)
    provider = AnthropicProvider(api_key="sk-test")
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        count = provider.count_input_tokens(
            system="be precise", messages=[Message(role="user", content="hi")], tools=[_cache_tool()],
        )
    assert count.tokens == 123
    assert count.kind == "native"
    assert count.method == "anthropic-messages-count_tokens"
    assert fake_client.messages.count_tokens.call_args.kwargs["tools"][0]["name"] == "calc"


def test_anthropic_provider_tool_call() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response(
        text="",
        tool_use={"id": "t1", "name": "calc", "input": {"x": 1}},
    )
    provider = AnthropicProvider(api_key="sk-test")
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
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
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        provider.complete(system="x", messages=messages, tools=[])

    sent = fake_client.messages.create.call_args.kwargs["messages"]
    # Should be: user, assistant (with two tool_use blocks), user (with two tool_result blocks)
    assert sent[0]["role"] == "user"
    assert sent[1]["role"] == "assistant"
    assert len([b for b in sent[1]["content"] if b["type"] == "tool_use"]) == 2
    assert sent[2]["role"] == "user"
    tool_result_blocks = [b for b in sent[2]["content"] if b["type"] == "tool_result"]
    assert len(tool_result_blocks) == 2


def test_anthropic_provider_sets_cache_breakpoints() -> None:
    """With caching on (default), the system prompt and the last tool carry a
    cache_control breakpoint so the static preamble is read from cache."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response("ok")
    provider = AnthropicProvider(api_key="sk-test")
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        provider.complete(system="be nice", messages=[Message(role="user", content="hi")], tools=[_cache_tool()])

    kwargs = fake_client.messages.create.call_args.kwargs
    # system becomes a list of blocks with a breakpoint
    assert isinstance(kwargs["system"], list)
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["system"][0]["text"] == "be nice"
    # the last tool carries the breakpoint (covers the whole tools block)
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_provider_caching_can_be_disabled() -> None:
    """cache_prompt=False sends a plain string system and unannotated tools."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response("ok")
    provider = AnthropicProvider(api_key="sk-test", cache_prompt=False)
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        provider.complete(system="be nice", messages=[Message(role="user", content="hi")], tools=[_cache_tool()])

    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["system"] == "be nice"
    assert "cache_control" not in kwargs["tools"][-1]


def test_anthropic_provider_surfaces_cache_usage() -> None:
    """cache_read / cache_creation token counts flow through into usage."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response(
        "ok",
        usage=SimpleNamespace(
            input_tokens=3, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=1200,
        ),
    )
    provider = AnthropicProvider(api_key="sk-test")
    with patch("ronin_agent_patterns.providers.anthropic_provider.anthropic.Anthropic", return_value=fake_client):
        resp = provider.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])

    assert resp.usage["cache_read_input_tokens"] == 1200
    assert resp.usage["cache_creation_input_tokens"] == 0


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

    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="gpt-4o-mini", api_key="sk-x")
        result = provider.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])

    assert result.text == "hi"
    assert result.stop_reason == "end_turn"
    assert result.usage["input_tokens"] == 12

    # Verify auth header was set
    headers = fake_client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer sk-x"


def test_openai_provider_uses_responses_native_token_counter() -> None:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = {"input_tokens": 88}
    response.raise_for_status = MagicMock()
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = response

    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="gpt-test", api_key="sk-x")
        count = provider.count_input_tokens(
            system="s", messages=[Message(role="user", content="hi")], tools=[_cache_tool()],
        )

    assert count.tokens == 88
    assert count.kind == "native"
    assert fake_client.post.call_args.args[0].endswith("/responses/input_tokens")
    body = fake_client.post.call_args.kwargs["json"]
    assert body["instructions"] == "s"
    assert body["tools"][0]["name"] == "calc"


def test_openai_compat_fallback_count_is_explicit_estimate() -> None:
    provider = OpenAICompatProvider(model="llama3.1", base_url="http://localhost:11434/v1", effort_provider="ollama")
    count = provider.count_input_tokens(
        system="s", messages=[Message(role="user", content="hello")], tools=[],
    )
    assert count.kind == "estimated"
    assert "estimate" in count.method


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

    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
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
    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
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
    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="gpt-4o-mini", api_key="sk-x")
        provider.complete(system="s", messages=messages, tools=[])

    sent = fake_client.post.call_args.kwargs["json"]["messages"]
    # system + user + assistant (with tool_calls) + tool
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[2]["tool_calls"][0]["function"]["name"] == "search"
    assert sent[3]["tool_call_id"] == "t1"


def test_openai_compat_retries_on_429(monkeypatch) -> None:
    """A transient 429 is retried (with backoff) instead of failing the turn."""
    import ronin_agent_patterns.providers.openai_compat as oc
    monkeypatch.setattr(oc.time, "sleep", lambda *_a, **_k: None)  # no real waiting

    r429 = MagicMock(spec=httpx.Response)
    r429.status_code = 429
    r429.headers = {"retry-after": "0"}
    ok = _openai_response(content="recovered")

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.side_effect = [r429, r429, ok]  # two limits, then success

    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="m", api_key="sk-x")
        result = provider.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])

    assert result.text == "recovered"
    assert fake_client.post.call_count == 3


def test_openai_compat_on_retry_callback_fires(monkeypatch) -> None:
    """A 429 fires the on_retry callback (so the CLI can show 'retrying in Ns')
    then the call succeeds on the next attempt."""
    import ronin_agent_patterns.providers.openai_compat as oc
    monkeypatch.setattr(oc.time, "sleep", lambda *_a, **_k: None)

    r429 = MagicMock(spec=httpx.Response)
    r429.status_code = 429
    r429.headers = {"retry-after": "7"}
    ok = _openai_response(content="recovered")

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.side_effect = [r429, ok]

    calls: list = []
    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(
            model="m", api_key="sk-x",
            on_retry=lambda attempt, wait, status: calls.append((attempt, wait, status)),
        )
        result = provider.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])

    assert result.text == "recovered"
    assert len(calls) == 1
    assert calls[0][0] == 1            # attempt number
    assert calls[0][1] == 7.0          # honoured Retry-After
    assert calls[0][2] == 429          # status


def test_openai_compat_roundtrips_tool_call_provider_meta() -> None:
    """Gemini-style extra_content (thought_signature) is captured on parse and
    replayed when the assistant tool-call is serialized back."""
    from ronin_agent_patterns.providers.openai_compat import _to_openai_messages

    sig = {"google": {"thought_signature": "SIG"}}
    resp = _openai_response(content="", tool_calls=[{
        "id": "c1", "type": "function",
        "function": {"name": "f", "arguments": "{}"},
        "extra_content": sig,
    }])
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = resp
    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        prov = OpenAICompatProvider(model="m", api_key="k")
        r = prov.complete(system="s", messages=[Message(role="user", content="hi")], tools=[])

    tc = r.tool_calls[0]
    assert tc.provider_meta == {"extra_content": sig}

    msgs = _to_openai_messages("sys", [Message(role="assistant", content="", tool_calls=[tc])])
    asst = next(m for m in msgs if m["role"] == "assistant")
    assert asst["tool_calls"][0]["extra_content"] == sig
