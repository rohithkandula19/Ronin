"""Shared fixtures for the context tests: trees on disk and scripted transcripts.

Named ``context_harness`` rather than ``harness`` because the test directories are
deliberately not packages, so every module here is importable by its bare basename
and two directories may not share one.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ronin.core.types import Message, Role, Text, ToolResultBlock, ToolUse


def plant(root: Path, tree: Mapping[str, str], *, git: bool = True) -> Path:
    """Write ``{relative path: content}`` under ``root``, creating parents.

    Writes bytes so a test can assert on line endings without universal-newline
    translation quietly normalizing them first.
    """
    root.mkdir(parents=True, exist_ok=True)
    if git:
        (root / ".git").mkdir(exist_ok=True)
    for relative, content in tree.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    return root


def user(text: str) -> Message:
    return Message(role=Role.USER, content_blocks=(Text(text),))


def assistant(text: str = "", *calls: ToolUse) -> Message:
    blocks: list[Text | ToolUse] = []
    if text:
        blocks.append(Text(text))
    blocks.extend(calls)
    return Message(role=Role.ASSISTANT, content_blocks=tuple(blocks))


def tool_results(*results: ToolResultBlock) -> Message:
    return Message(role=Role.TOOL, content_blocks=tuple(results))


def write_call(call_id: str, path: str, content: str) -> ToolUse:
    return ToolUse(id=call_id, name="write", arguments={"path": path, "content": content})


def scripted_session(
    turns: int, *, marked_turn: int = 3, marked_path: str = "src/turn3.py"
) -> list[Message]:
    """A ``turns``-turn transcript: user request, tool call, tool result, repeat.

    ``marked_turn`` writes a distinctive sentinel into ``marked_path`` and nothing
    touches that file again — which is exactly the fact per-path retention has to
    preserve through every later compaction.
    """
    messages: list[Message] = [
        Message(
            role=Role.SYSTEM,
            content_blocks=(Text("system prompt + repo map + RONIN.md"),),
        )
    ]
    for turn in range(1, turns + 1):
        marked = turn == marked_turn
        path = marked_path if marked else f"src/file{turn}.py"
        content = (
            f"def answer() -> int:\n    return 42  # SENTINEL turn {marked_turn}\n"
            if marked
            else f"# routine turn {turn}\n"
        )
        call_id = f"call-{turn}"
        messages.append(user(f"turn {turn}: edit {path}"))
        messages.append(assistant(f"editing {path}", write_call(call_id, path, content)))
        messages.append(
            tool_results(
                ToolResultBlock(tool_use_id=call_id, content=f"wrote {path}\n{content}")
            )
        )
    return messages


def transcript_text(messages: Sequence[Message]) -> str:
    """Everything the model can still read, as one string."""
    parts: list[str] = []
    for message in messages:
        for block in message.content_blocks:
            if isinstance(block, Text):
                parts.append(block.text)
            elif isinstance(block, ToolResultBlock):
                parts.append(block.content)
            elif isinstance(block, ToolUse):
                parts.append(repr(dict(block.arguments)))
    return "\n".join(parts)


async def fake_summarizer(prompt: str) -> str:
    """A summarizer that returns the required five sections and nothing else."""
    return (
        "## Goal\nship the feature\n\n"
        "## Files touched\n- src/a.py — edited\n\n"
        "## Learned\n- the tests live in tests/\n\n"
        "## Failed\n- one lint run failed\n\n"
        f"## Remaining\n- finish up (prompt was {len(prompt)} chars)\n"
    )
