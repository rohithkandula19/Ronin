"""See persistence and replay work: ``python -m ronin.persistence.demo``.

Offline, in a throwaway temp dir, with no provider and no model. The events are
hand-built in exactly the order ``core.loop`` emits them — including a
``StreamReset``, because a session that never retried its stream never exercises
the rule most likely to be wrong.

What it proves, in order: a real multi-turn session is written; the file is
truncated mid-line the way a crash truncates it; the load reports the torn line
instead of raising; the replay repairs the tool call the crash left unanswered and
the resulting state is sendable; the session picker lists both sessions without
reading their events; and both exporters produce a file, the HTML with no external
reference in it.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ..core.types import (
    AgentState,
    ApprovalRequest,
    Budget,
    DangerLevel,
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
    ToolResultBlock,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
)
from .export import to_html, to_markdown
from .resume import replay, resume_latest, sessions_for_cwd
from .transcript import (
    SessionMeta,
    Transcript,
    list_sessions,
    read_events,
    sessions_dir,
)

MODEL = "fake-model-for-the-demo"
SESSION_ID = "20260809-120000-aaa111"
OTHER_ID = "20260809-120500-bbb222"

READ_ARGS = {"path": "src/app.py"}
READ_OUTPUT = "1\tdef handler():\n2\t    return None\n"
WRITE_ARGS = {"path": "src/app.py", "content": "def handler():\n    return Response()\n"}
SIGNATURE = "sig-OPAQUE-42=="


def rule(title: str) -> None:
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


def first_prompt_state() -> AgentState:
    """The state the orchestrator hands the loop for the first prompt."""
    return AgentState(
        messages=(
            Message(role=Role.USER, content_blocks=(Text(text="Why does handler() 404?"),)),
        ),
        todos=(Todo(id="1", subject="find the bug", status=TodoStatus.IN_PROGRESS),),
        cwd=".",
        budget=Budget(max_usd=5.0, spent_tokens=1_240, spent_usd=0.0182),
        mode=Mode.ASK,
        checkpoint_id="ckpt-a1",
    )


def first_turn_events(state: AgentState) -> tuple[Event, ...]:
    """One ``run_turn`` call: iteration 0 calls a tool, iteration 1 answers.

    Iteration 0 re-streams: a first sentence is emitted, the provider drops the
    connection, and the failover re-streams from scratch behind a ``StreamReset``.
    A replay that ignores that reset shows both sentences.
    """
    read_use = ToolUse(id="toolu_read_1", name="read", arguments=READ_ARGS)
    read_result = ToolResult(
        ok=True, content=READ_OUTPUT, artifacts=("src/app.py",), tokens_estimate=24
    )
    answer = "handler() returns None, so the framework 404s. I can fix it."
    ended = AgentState(
        messages=(
            *state.messages,
            Message(
                role=Role.ASSISTANT,
                content_blocks=(
                    Thinking(text="Check what handler returns.", signature=SIGNATURE),
                    Text(text="Reading src/app.py to see what handler returns."),
                    read_use,
                ),
            ),
            Message(role=Role.TOOL, content_blocks=(read_result.as_block(read_use.id),)),
            Message(role=Role.ASSISTANT, content_blocks=(Text(text=answer),)),
        ),
        todos=state.todos,
        cwd=state.cwd,
        budget=Budget(max_usd=5.0, spent_tokens=3_910, spent_usd=0.0461),
        mode=state.mode,
        checkpoint_id="ckpt-a2",
    )
    return (
        TurnStart(turn_index=0),
        TextDelta(text="Let me grep the router first — "),
        StreamReset(reason="provider dropped the stream mid-sentence; failed over"),
        TextDelta(text="Check what handler returns.", thinking=True),
        TextDelta(text="Reading src/app.py to see what handler returns."),
        ToolStart(tool_use_id=read_use.id, name="read", arguments=READ_ARGS),
        ToolEnd(tool_use_id=read_use.id, name="read", result=read_result),
        TurnStart(turn_index=1),
        TextDelta(text=answer),
        TurnEnd(
            turn_index=1,
            state=TurnState.DONE,
            stop_reason="no_tool_calls",
            agent_state=ended,
        ),
    )


def interrupted_turn_events() -> tuple[Event, ...]:
    """The second prompt's turn, which the crash lands in the middle of.

    No ``TurnEnd``: the process died with the result half-written. This is the shape
    replay has to repair rather than reject.
    """
    return (
        TurnStart(turn_index=0),
        TextDelta(text="Applying the fix to src/app.py."),
        ToolStart(tool_use_id="toolu_write_2", name="write", arguments=WRITE_ARGS),
        ApprovalRequest(
            tool_use_id="toolu_write_2",
            name="write",
            danger_level=DangerLevel.MUTATING,
            rendered='write(src/app.py) — 2 lines, replacing "return None"',
            reason="mutating",
        ),
        ToolEnd(
            tool_use_id="toolu_write_2",
            name="write",
            result=ToolResult(ok=True, content="wrote 2 lines", artifacts=("src/app.py",)),
        ),
    )


def adversarial_events() -> tuple[Event, ...]:
    """Content designed to break an unescaped export."""
    return (
        TurnStart(turn_index=0),
        TextDelta(text='<script>alert("xss")</script>'),
        ToolStart(tool_use_id="toolu_x", name="read", arguments={"path": "evil.html"}),
        ToolEnd(
            tool_use_id="toolu_x",
            name="read",
            result=ToolResult(ok=True, content="</details><img onerror='x'>"),
        ),
        TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"),
    )


def crash_mid_line(path: Path) -> int:
    """Cut the file inside its last line, the way a crash during ``write`` does."""
    data = path.read_bytes()
    last_newline = data.rfind(b"\n", 0, len(data) - 1)
    keep = last_newline + 1 + (len(data) - last_newline - 1) // 3
    path.write_bytes(data[:keep])
    return len(data) - keep


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ronin-persistence-") as raw:
        root = Path(raw)
        directory = sessions_dir(root)
        project = root / "project"
        other = root / "another-checkout"
        project.mkdir()
        other.mkdir()

        # ---------------------------------------------------------- 1. record
        rule("1. record a real multi-turn session (append-only, fsync per turn)")
        state = first_prompt_state()
        events = (*first_turn_events(state), *interrupted_turn_events())
        with Transcript.open(
            directory,
            SESSION_ID,
            model=MODEL,
            cwd=str(project),
            title="fix the 404",
        ) as transcript:
            transcript.extend(events)
            path = transcript.path
            meta = transcript.meta
        print(f"  {path.name}: {len(events)} events, {meta.turns} completed turn(s)")
        print(f"  cost ${meta.cost_usd:.4f}, checkpoints {list(meta.checkpoint_ids)}, "
              f"files touched {list(meta.files_touched)}")

        with Transcript.open(
            directory, OTHER_ID, model=MODEL, cwd=str(other), title="unrelated work"
        ) as elsewhere:
            elsewhere.extend(
                (
                    TurnStart(turn_index=0),
                    TextDelta(text="Nothing to do here."),
                    TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"),
                )
            )
        print(f"  a second session recorded in {other.name}/, so --continue must choose")

        on_disk = path.read_bytes()
        newlines = on_disk.count(b"\n")
        has_crlf = b"\r\n" in on_disk
        print(f"  on disk: {len(on_disk)} bytes, {newlines} lines, CRLF present: {has_crlf}")

        # ------------------------------------------------------- 2. the crash
        rule("2. truncate the last line mid-write, the way a crash does")
        lost = crash_mid_line(path)
        print(f"  cut {lost} bytes out of the final record; the file now ends mid-line")

        # -------------------------------------------------------- 3. load it
        rule("3. load it — the torn line is reported, not raised")
        read = read_events(path)
        header = read.header or SessionMeta(session_id=SESSION_ID)
        print(f"  loaded {len(read.events)} events; header says model={header.model!r} "
              f"cwd={Path(header.cwd).name!r}")
        for note in read.skipped:
            print(f"  skipped  → {note}")

        # ---------------------------------------------------------- 4. replay
        rule("4. replay to a sendable AgentState, repairing the interrupted call")
        result = replay(read.events)
        print(f"  state source: {result.source.value}; {len(result.turns)} turn(s) replayed")
        for note in result.notes:
            print(f"  note     → {note}")
        for note in result.repairs:
            print(f"  repair   → {note}")
        for note in result.mismatches:
            print(f"  mismatch → {note}")
        else_ok = "empty — the transcript is sendable" if not result.state.pairing_errors else "NOT EMPTY"
        print(f"  pairing_errors: {else_ok}")
        reset_turn = result.turns[0]
        print(f"  turn 1 text after {reset_turn.resets} StreamReset: {reset_turn.text!r}")
        print("  → the pre-reset sentence is gone; a mishandled reset would show both.")

        tail = result.state.messages[-1]
        repaired = [b for b in tail.content_blocks if isinstance(b, ToolResultBlock)]
        for block in repaired:
            print(f"  repaired block: id={block.tool_use_id} is_error={block.is_error}")
            print(f"    {block.content}")
        signatures = [
            block.signature
            for message in result.state.messages
            for block in message.content_blocks
            if isinstance(block, Thinking)
        ]
        print(f"  thinking signatures round-tripped byte-identical: {signatures}")
        budget = result.state.budget
        print(f"  budget carried: ${budget.spent_usd:.4f} of ${budget.max_usd}, "
              f"mode={result.state.mode.value}, checkpoint={result.state.checkpoint_id}, "
              f"todos={[(t.id, t.status.value) for t in result.state.todos]}")

        # ----------------------------------------------------- 5. the picker
        rule("5. the picker reads sidecars only; --continue filters on cwd")
        for row in list_sessions(directory):
            print(f"  {row.session_id}  {row.turns} turn(s)  ${row.cost_usd:.4f}  "
                  f"{row.duration_seconds:.2f}s  {row.title!r}  cwd={Path(row.cwd).name}")
        here = sessions_for_cwd(directory, str(project))
        print(f"  sessions in {project.name}/: {[row.session_id for row in here]}")
        continued = resume_latest(directory, str(project))
        if continued is not None:
            print(f"  --continue picks {continued.meta.session_id}: "
                  f"{len(continued.events)} events, {len(continued.skipped)} skipped line(s), "
                  f"{len(continued.state.messages)} messages restored")

        # ---------------------------------------------------------- 6. export
        rule("6. export: Markdown, and one self-contained HTML file")
        markdown = to_markdown(read.events, header)
        html = to_html(read.events, header)
        md_path = root / "session.md"
        html_path = root / "session.html"
        md_path.write_text(markdown, encoding="utf-8")
        html_path.write_text(html, encoding="utf-8")
        external = [
            marker
            for marker in ("<script", "<link", "http://", "https://", "src=", "@import")
            if marker in html
        ]
        print(f"  {md_path.name}:   {md_path.stat().st_size} bytes, "
              f"{markdown.count('```') // 2} fenced block(s)")
        print(f"  {html_path.name}: {html_path.stat().st_size} bytes, "
              f"{html.count('<details')} collapsed tool block(s)")
        print(f"  external references in the HTML: {external or 'none'}")

        hostile = to_html(adversarial_events(), header)
        print("  adversarial fixture: "
              f"<script> escaped: {'<script' not in hostile}, "
              f"</details> escaped: {'&lt;/details&gt;' in hostile}")

    rule("done")
    print("A crash mid-write is a reportable event, not a lost session: the torn line")
    print("is named, the unanswered tool call is answered, and the state that comes")
    print("out is one a provider will accept.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
