"""Shared fixtures for the persistence tests. Named for its package on purpose.

``tests/`` is not a package, so every module in it is importable by its bare
basename and two test directories cannot share one — a second ``harness.py``
shadows the first and breaks whichever suite pytest imported later.

Everything here builds *real* core values. There is no provider fake because this
layer has no provider seam: it takes events and gives back events.
"""

from __future__ import annotations

from pathlib import Path

from ronin.core.types import (
    AgentState,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Error,
    Event,
    Message,
    Mode,
    Role,
    StreamReset,
    Text,
    TextDelta,
    Thinking,
    Todo,
    TodoStatus,
    ToolEnd,
    ToolResult,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
)

SIGNATURE = "sig/opaque+42==\nsecond line\ttab é ✓"
NESTED_ARGS = {
    "path": "src/app.py",
    "options": {"limit": 10, "deep": {"flags": [1, 2.5, True, None, "x"]}},
    "empty": {},
}

READ_USE = ToolUse(id="toolu_read_1", name="read", arguments=NESTED_ARGS)
READ_RESULT = ToolResult(
    ok=True, content="1\tdef handler():\n", artifacts=("src/app.py",), tokens_estimate=7
)


def sample_event(event_type: type) -> Event:
    """One populated instance of every member of the ``Event`` union.

    Keyed by type so the codec-coverage test can build a sample for whatever
    ``EVENT_TYPES`` currently holds and fail loudly on a member with no sample.
    """
    samples: dict[type, Event] = {
        TurnStart: TurnStart(turn_index=3, state=TurnState.THINKING),
        TextDelta: TextDelta(text="hello ✓\nworld", thinking=True),
        StreamReset: StreamReset(reason="failover after 12 tokens"),
        ToolStart: ToolStart(tool_use_id="toolu_1", name="read", arguments=NESTED_ARGS),
        ToolEnd: ToolEnd(
            tool_use_id="toolu_1",
            name="read",
            result=ToolResult(ok=False, error="ENOENT: no such file", artifacts=("a", "b")),
        ),
        ApprovalRequest: ApprovalRequest(
            tool_use_id="toolu_1",
            name="bash",
            danger_level=DangerLevel.DESTRUCTIVE,
            rendered="rm -rf build/",
            reason="destructive",
        ),
        TurnEnd: TurnEnd(
            turn_index=2,
            state=TurnState.INTERRUPTED,
            stop_reason="interrupted",
            agent_state=full_state(),
        ),
        Error: Error(message="model stream ended", kind="protocol", recoverable=True),
    }
    if event_type not in samples:  # pragma: no cover - guard for a future union member
        raise AssertionError(
            f"no sample event for {event_type.__name__}; add one to persistence_harness "
            f"so the codec round-trip test covers it"
        )
    return samples[event_type]


def full_state() -> AgentState:
    """An ``AgentState`` exercising every field and all four block types."""
    return AgentState(
        messages=(
            Message(role=Role.USER, content_blocks=(Text(text="why the 404?"),)),
            Message(
                role=Role.ASSISTANT,
                content_blocks=(
                    Thinking(text="reason ✓", signature=SIGNATURE),
                    Text(text="Reading the file."),
                    READ_USE,
                ),
                metadata={"kind": "stall_nudge", "n": 3, "ratio": 0.5},
            ),
            Message(role=Role.TOOL, content_blocks=(READ_RESULT.as_block(READ_USE.id),)),
        ),
        todos=(
            Todo(id="1", subject="find it", status=TodoStatus.COMPLETED),
            Todo(id="2", subject="fix it", status=TodoStatus.IN_PROGRESS),
        ),
        cwd="/work/repo",
        budget=Budget(
            max_tokens=100_000,
            max_usd=2.5,
            max_wall_seconds=600.0,
            spent_tokens=4_211,
            spent_usd=0.1234567,
            elapsed_seconds=12.75,
        ),
        mode=Mode.AUTO_EDIT,
        checkpoint_id="ckpt-9",
    )


def two_turn_session() -> tuple[Event, ...]:
    """A clean, completed session: reset, tool call, answer, checkpointed ``TurnEnd``."""
    answer = "handler() returns None."
    ended = AgentState(
        messages=(
            Message(role=Role.USER, content_blocks=(Text(text="why the 404?"),)),
            Message(
                role=Role.ASSISTANT,
                content_blocks=(
                    Thinking(text="look at it", signature=SIGNATURE),
                    Text(text="Reading src/app.py."),
                    READ_USE,
                ),
            ),
            Message(role=Role.TOOL, content_blocks=(READ_RESULT.as_block(READ_USE.id),)),
            Message(role=Role.ASSISTANT, content_blocks=(Text(text=answer),)),
        ),
        cwd="/work/repo",
        budget=Budget(max_usd=5.0, spent_tokens=900, spent_usd=0.0231),
        checkpoint_id="ckpt-1",
    )
    return (
        TurnStart(turn_index=0),
        TextDelta(text="DISCARD ME — pre-reset prose. "),
        StreamReset(reason="provider dropped the stream; failed over"),
        TextDelta(text="look at it", thinking=True),
        TextDelta(text="Reading src/app.py."),
        ToolStart(tool_use_id=READ_USE.id, name="read", arguments=NESTED_ARGS),
        ToolEnd(tool_use_id=READ_USE.id, name="read", result=READ_RESULT),
        TurnStart(turn_index=1),
        TextDelta(text=answer),
        TurnEnd(
            turn_index=1,
            state=TurnState.DONE,
            stop_reason="no_tool_calls",
            agent_state=ended,
        ),
    )


def interrupted_tail() -> tuple[Event, ...]:
    """A turn that starts a mutating tool and never records its result."""
    return (
        TurnStart(turn_index=0),
        TextDelta(text="Applying the fix."),
        ToolStart(tool_use_id="toolu_write_2", name="write", arguments={"path": "src/app.py"}),
        ApprovalRequest(
            tool_use_id="toolu_write_2",
            name="write",
            danger_level=DangerLevel.MUTATING,
            rendered="write(src/app.py)",
            reason="mutating",
        ),
    )


def crashed_session() -> tuple[Event, ...]:
    """The normal post-crash shape: a checkpointed turn, then an unfinished one."""
    return (*two_turn_session(), *interrupted_tail())


def adversarial_session() -> tuple[Event, ...]:
    """Content chosen to break an export that forgets to escape.

    Every string here has appeared in a real transcript in spirit: model output that
    quotes HTML, and file contents that are HTML.
    """
    return (
        TurnStart(turn_index=0),
        TextDelta(text='<script>alert("xss")</script> & <b>bold</b>'),
        ToolStart(
            tool_use_id="toolu_evil",
            name="read",
            arguments={"path": 'evil".html', "pattern": "</details>"},
        ),
        ToolEnd(
            tool_use_id="toolu_evil",
            name="read",
            result=ToolResult(
                ok=False,
                error="</details><img src=x onerror=\"alert('1')\"> 'single' \"double\"",
            ),
        ),
        Error(message="<script>oops</script>", kind="tool", recoverable=True),
        TurnEnd(turn_index=0, state=TurnState.ERROR, stop_reason="<b>stopped</b>"),
    )


def truncate_mid_line(path: Path, fraction: float = 0.5) -> int:
    """Cut inside the file's final line and return how many bytes were lost."""
    data = path.read_bytes()
    start_of_last = data.rfind(b"\n", 0, len(data) - 1) + 1
    body = len(data) - start_of_last
    keep = start_of_last + max(1, int(body * fraction))
    path.write_bytes(data[:keep])
    return len(data) - keep
