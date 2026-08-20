"""The **request** direction of the openai-compatible adapter.

`test_golden_streams.py` replays raw provider bytes from disk and asserts the normalized
output — 522 lines of the *read* path. The *write* path had nothing: **no test in
`tests/providers/` referenced `tool_call_id` or `"tool_calls"`**, so `_render_message` and
`_render_tool` were unverified for the one adapter that covers openai, deepseek, together,
groq, openrouter, vllm, llama.cpp, lmstudio and ollama.

That asymmetry matters more than the line count suggests. A wrong *response* parse fails
loudly on the turn it happens. A wrong *request* payload is accepted by the client, sent,
and comes back as a provider 400 several turns later — far from the cause, and looking like
a model problem rather than an encoding one. The module's own docstring names the invariant
that protects against it:

    ``tool_call_id`` is copied verbatim and never regenerated

which is exactly the kind of promise that needs a test rather than a comment.
"""

from __future__ import annotations

import json
from typing import Any

from ronin.core.types import (
    Message,
    Role,
    Text,
    ToolResultBlock,
    ToolSpec,
    ToolUse,
)
from ronin.providers.openai_compat import _render_message, _render_tool


def rendered(message: Message) -> list[dict[str, Any]]:
    return list(_render_message(message))


# --------------------------------------------------------------------------- #
# Tool results
# --------------------------------------------------------------------------- #


def test_a_tool_result_becomes_its_own_tool_role_turn_with_the_id_verbatim() -> None:
    """The invariant the module docstring promises.

    A regenerated or normalized id is the classic cause of a 400 that arrives turns
    later: the provider matches results to calls by this string and nothing else.
    """
    weird = "call_AbC-123_/=+xyz"  # deliberately not a shape we would ever mint
    message = Message(
        role=Role.TOOL,
        content_blocks=(ToolResultBlock(tool_use_id=weird, content="file contents"),),
    )
    (entry,) = rendered(message)
    assert entry == {"role": "tool", "tool_call_id": weird, "content": "file contents"}


def test_several_tool_results_become_several_turns_in_order() -> None:
    """OpenAI's wire format has no batch form: one `tool` turn per result, and the
    order has to survive because it is how a reader pairs them up."""
    message = Message(
        role=Role.TOOL,
        content_blocks=(
            ToolResultBlock(tool_use_id="t1", content="first"),
            ToolResultBlock(tool_use_id="t2", content="second"),
        ),
    )
    entries = rendered(message)
    assert [e["tool_call_id"] for e in entries] == ["t1", "t2"]
    assert [e["content"] for e in entries] == ["first", "second"]
    assert {e["role"] for e in entries} == {"tool"}


def test_a_failed_tool_result_is_still_sent_as_content_not_dropped() -> None:
    """`is_error` has no representation in this wire format, so the text must carry
    it. Dropping the turn would leave the call unanswered and break the next request."""
    message = Message(
        role=Role.TOOL,
        content_blocks=(
            ToolResultBlock(tool_use_id="t1", content="ENOENT: no such file", is_error=True),
        ),
    )
    (entry,) = rendered(message)
    assert entry["tool_call_id"] == "t1"
    assert "ENOENT" in entry["content"]


# --------------------------------------------------------------------------- #
# Assistant turns with tool calls
# --------------------------------------------------------------------------- #


def test_an_assistant_tool_call_is_rendered_as_a_function_call() -> None:
    message = Message(
        role=Role.ASSISTANT,
        content_blocks=(
            Text("I'll read it."),
            ToolUse(id="t1", name="read_file", arguments={"path": "a.py"}),
        ),
    )
    (entry,) = rendered(message)
    assert entry["role"] == "assistant"
    assert entry["content"] == "I'll read it."
    (call,) = entry["tool_calls"]
    assert call["id"] == "t1"
    assert call["type"] == "function"
    assert call["function"]["name"] == "read_file"
    # Arguments go over the wire as a JSON *string*, not an object.
    assert json.loads(call["function"]["arguments"]) == {"path": "a.py"}


def test_tool_call_arguments_are_serialized_with_sorted_keys() -> None:
    """Determinism here is not cosmetic — it is what makes a prompt prefix stable.

    Two identical calls whose JSON differs only in key order produce different bytes,
    which breaks a provider's prefix cache and silently doubles cost.
    """
    message = Message(
        role=Role.ASSISTANT,
        content_blocks=(ToolUse(id="t1", name="grep", arguments={"z": 1, "a": 2, "m": 3}),),
    )
    (entry,) = rendered(message)
    assert entry["tool_calls"][0]["function"]["arguments"] == '{"a": 2, "m": 3, "z": 1}'


def test_parallel_tool_calls_are_rendered_in_one_assistant_turn() -> None:
    message = Message(
        role=Role.ASSISTANT,
        content_blocks=(
            ToolUse(id="t1", name="read_file", arguments={"path": "a.py"}),
            ToolUse(id="t2", name="read_file", arguments={"path": "b.py"}),
        ),
    )
    (entry,) = rendered(message)
    assert [c["id"] for c in entry["tool_calls"]] == ["t1", "t2"]


def test_an_assistant_turn_without_tool_calls_carries_no_tool_calls_key() -> None:
    """An empty `tool_calls: []` is not the same as its absence; some servers reject it."""
    (entry,) = rendered(Message(role=Role.ASSISTANT, content_blocks=(Text("just prose"),)))
    assert entry == {"role": "assistant", "content": "just prose"}
    assert "tool_calls" not in entry


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #


def test_system_and_user_roles_map_straight_through() -> None:
    (system,) = rendered(Message(role=Role.SYSTEM, content_blocks=(Text("be terse"),)))
    (user,) = rendered(Message(role=Role.USER, content_blocks=(Text("fix the bug"),)))
    assert system == {"role": "system", "content": "be terse"}
    assert user == {"role": "user", "content": "fix the bug"}


def test_an_empty_message_renders_nothing_rather_than_an_empty_turn() -> None:
    """A `{"role": "user", "content": ""}` turn is wasted tokens at best and a
    validation error at worst."""
    assert rendered(Message(role=Role.USER)) == []
    assert rendered(Message(role=Role.USER, content_blocks=(Text(""),))) == []


# --------------------------------------------------------------------------- #
# Tool specs
# --------------------------------------------------------------------------- #


def test_a_tool_spec_renders_as_an_openai_function() -> None:
    spec = ToolSpec(
        name="read_file",
        description="Read a file.",
        json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    entry = _render_tool(spec)
    assert entry["type"] == "function"
    assert entry["function"]["name"] == "read_file"
    assert entry["function"]["description"] == "Read a file."
    assert entry["function"]["parameters"]["properties"]["path"]["type"] == "string"


def test_a_tool_with_no_schema_still_gets_a_valid_parameters_object() -> None:
    """An empty `parameters` is rejected by several of the nine servers this adapter
    covers, so the fallback is a real object rather than `{}`."""
    entry = _render_tool(ToolSpec(name="ping", description="Ping."))
    assert entry["function"]["parameters"] == {"type": "object", "properties": {}}
