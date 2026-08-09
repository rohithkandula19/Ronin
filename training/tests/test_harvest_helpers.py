"""Builders shared by the harvest tests. No tests of its own — helpers only.

These write **real** transcripts: real ``ronin.core.types`` events through the real
``ronin.persistence.Transcript`` writer, read back through the real ``read_events``.
A hand-built dict fixture would prove the miner works on a fixture; this proves it
works on the file format the eval runner actually leaves behind.

Named ``test_harvest_helpers`` because the harvest agent owns only
``training/tests/test_harvest_*.py``, and because every module under a test tree in
this repo is importable by its bare basename — nothing else in the workspace starts
with ``test_harvest_``.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ronin_training.harvest.sources import RunManifest, read_transcript_events
from ronin_training.harvest.trajectory import ToolSchema, Trajectory
from ronin_training.validators import load_registry

# The agent tree is on sys.path via the root pytest config (`pythonpath = ["src"]`).
from ronin.core.types import (
    ApprovalRequest,
    DangerLevel,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.persistence import Transcript, read_events


def registry_tools(*names: str) -> tuple[ToolSchema, ...]:
    """Real tool schemas from ``training/config/tool_registry.json``.

    Real rather than invented: a miner tested against a made-up schema proves nothing
    about whether it can spot a call that violates a schema Ronin actually declares.
    """
    registry = load_registry()
    return tuple(
        ToolSchema(
            name=registry[name]["name"],
            description=registry[name]["description"],
            parameters=registry[name]["parameters"],
        )
        for name in names
    )


def turn(
    *,
    text: str = "",
    calls: Sequence[tuple[str, str, dict[str, Any]]] = (),
    results: Sequence[tuple[str, str, bool, str, str]] = (),
    index: int = 0,
    gated: Sequence[str] = (),
) -> list[Any]:
    """One turn's worth of real events, in the order the loop emits them.

    ``calls`` items are ``(id, name, arguments)``; ``results`` items are
    ``(id, name, ok, content, error)``; ``gated`` names the call ids that went
    through the approval gate, so an ``ApprovalRequest`` is recorded for them the
    way the loop records one.
    """
    events: list[Any] = [TurnStart(turn_index=index)]
    if text:
        events.append(TextDelta(text=text))
    by_id = {call_id: (name, args) for call_id, name, args in calls}
    for call_id, name, args in calls:
        if call_id in gated:
            events.append(
                ApprovalRequest(
                    tool_use_id=call_id,
                    name=name,
                    danger_level=DangerLevel.MUTATING,
                    rendered=f"{name} {args}",
                    reason="sensitive",
                )
            )
        events.append(ToolStart(tool_use_id=call_id, name=name, arguments=args))
    for call_id, name, ok, content, error in results:
        assert call_id in by_id, f"result for unknown call {call_id!r}"
        events.append(
            ToolEnd(
                tool_use_id=call_id,
                name=name,
                result=ToolResult(ok=ok, content=content, error=error),
            )
        )
    events.append(TurnEnd(turn_index=index, state=TurnState.DONE, stop_reason="no_tool_calls"))
    return events


def write_transcript(
    directory: Path, session_id: str, events: Sequence[Any], *, model: str = ""
) -> Path:
    """Persist ``events`` through the real writer and return the transcript path."""
    with Transcript.open(directory, session_id, model=model, cwd=str(directory)) as log:
        log.extend(events)
    path = log.path
    # Fail here rather than inside a test: an unreadable fixture would look like a
    # miner bug in every test that used it.
    assert read_events(path).events, f"{path} recorded nothing"
    return path


def load_recorded(
    directory: Path,
    session_id: str,
    events: Sequence[Any],
    *,
    task_id: str,
    prompt: str,
    tools: tuple[ToolSchema, ...],
    verified: bool = False,
    model: str = "fixture-model",
) -> Trajectory:
    """Write a real transcript, read it back, and return the harvested Trajectory."""
    from ronin_training.harvest.sources import load_run

    path = write_transcript(directory, session_id, events, model=model)
    return load_run(
        RunManifest(
            task_id=task_id,
            run_id=session_id,
            transcript=path,
            verified=verified,
            model=model,
            prompt=prompt,
            system="You are Ronin.",
            tools=tools,
        ),
        read=read_transcript_events,
    )
