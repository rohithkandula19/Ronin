"""The assistant-only loss mask — proven, not promised.

This is the test the whole SFT phase exists for: it builds labels for a realistic
conversation and asserts that every system, user, and tool-result token is -100 and only
the assistant tokens carry a real target. If this passes, the fine-tune cannot learn to
hallucinate tool output or write the user's turn.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from ronin_training.sft.masking import (
    IGNORE_INDEX,
    build_labels,
    default_render,
    masked_roles,
)


def _tok(text: str) -> Sequence[int]:
    """A trivial deterministic whitespace tokeniser: each word -> its length.

    Deterministic per text, so an assistant token's label equals its input id — which is
    exactly what the mask must preserve. No model, no weights.
    """
    return [len(word) for word in text.split()]


# A full multi-turn conversation: system prompt, user ask, assistant tool call, the tool's
# result, and a final assistant answer. Only the two assistant turns may be learned.
_CONVO: list[dict[str, Any]] = [
    {"role": "system", "content": "you are ronin follow the rules"},
    {"role": "user", "content": "read config and summarise it"},
    {
        "role": "assistant",
        "content": "reading it now",
        "tool_calls": [{"function": {"name": "read", "arguments": {"path": "config.toml"}}}],
    },
    {"role": "tool", "content": "port equals eight thousand and debug is false"},
    {"role": "assistant", "content": "the port is 8000 and debug is off"},
]


def test_every_non_assistant_token_is_ignored_and_assistant_tokens_are_learned() -> None:
    example = build_labels(_CONVO, _tok)

    # Reconstruct the per-message token counts and roles to check the mask span-by-span.
    cursor = 0
    for message in _CONVO:
        tokens = list(_tok(default_render(message)))
        span = example.labels[cursor : cursor + len(tokens)]
        if message["role"] == "assistant":
            assert span == tuple(tokens), f"assistant tokens must be learned, got {span}"
            assert IGNORE_INDEX not in span, "an assistant token was masked"
        else:
            assert all(label == IGNORE_INDEX for label in span), (
                f"{message['role']} tokens must be masked to -100, got {span}"
            )
        cursor += len(tokens)
    assert cursor == len(example.input_ids), "every token was accounted for"


def test_the_input_ids_keep_the_whole_conversation_even_where_masked() -> None:
    # The model still SEES the system/user/tool tokens (they are context); they are only
    # absent from the LOSS. So input_ids covers everything, labels mask most of it.
    example = build_labels(_CONVO, _tok)
    assert len(example.input_ids) == len(example.labels)
    assert example.masked_count > 0 and example.learn_count > 0
    assert example.masked_count > example.learn_count, "most of a turn is context, not target"


def test_a_conversation_with_no_learnable_assistant_token_is_refused() -> None:
    # A row with no assistant turn (or an empty one) teaches nothing; it must not slip in.
    with pytest.raises(ValueError, match="teaches nothing"):
        build_labels([{"role": "user", "content": "hi"}], _tok)
    with pytest.raises(ValueError, match="teaches nothing"):
        build_labels([{"role": "assistant", "content": ""}], _tok)


def test_masked_roles_names_everything_but_the_assistant() -> None:
    assert masked_roles(_CONVO) == ("system", "user", "tool")


def test_a_tool_call_only_assistant_turn_is_learnable() -> None:
    # An assistant turn that is *only* a tool call (no prose) still has learnable tokens —
    # the rendered call — so it is a valid target, not a fully-masked row.
    convo: list[dict[str, Any]] = [
        {"role": "user", "content": "list the files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "glob", "arguments": {"pattern": "*"}}}],
        },
    ]
    example = build_labels(convo, _tok)
    assert example.learn_count > 0
