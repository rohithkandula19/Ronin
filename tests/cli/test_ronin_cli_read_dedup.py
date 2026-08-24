"""A repeat read costs the file twice, and the guard that let it lied about why.

Before this, two things were true at once and only one of them was intended.

**The intended one:** the ``read`` tool's description asks the model not to re-read
a file it already read. That is advice. Nothing enforced it, nothing measured it,
and a model that ignored it paid full price for a second copy of the same bytes.

**The unintended one:** :class:`FileStateTracker` compares digests against *disk*
and has no view of the transcript, so "read at some point" and "still in front of
the model" were the same fact to it. They stop being the same fact the moment
compaction folds the middle of a session — and the divergence is reachable in
default configuration, with no retention bounds set at all, because retention keeps
*the most recent tool result per path* and a ``grep`` scoped to a file the model
already read overwrites the read that had the contents. The guard went on reporting
``UNCHANGED``: "safe to edit against the content you have", to a model holding a
three-line grep hit.

The fix makes the two facts one fact again, by dropping the record when the content
goes (:meth:`FileStateTracker.retain_only`, fed by
:func:`~ronin.context.compaction.paths_with_visible_read`). Dedup then rides on the
repaired record rather than on a second guess: a repeat read is answered with a stub
exactly when re-running it would return what the model already has.

Two deliberate limits on that condition, both here as tests because both are the
difference between saving tokens and lying:

* **The window is part of it.** A whole-file stub after a twenty-line read would
  claim content the model never saw.
* **The stub always names ``force``.** Every input to the condition is a fact about
  the world that could be stale in a way this process cannot see. One wasted call is
  recoverable; an edit written against a file the model never saw is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from gate_harness import RecordingRegistry, use

from ronin.cli.gate import FORCE_ARGUMENT, GatedRegistry, GateStage, gated
from ronin.context.compaction import (
    CLAMPED_KEY,
    RETAINED_PATH_KEY,
    CompactionPolicy,
    compact,
    paths_with_visible_read,
)
from ronin.context.filestate import (
    WHOLE_FILE,
    FileStateTracker,
    FileStatus,
    ReadWindow,
)
from ronin.core.types import Message, Role, Text, ToolResult, ToolResultBlock, ToolUse
from ronin.safety.injection import TaintTracker
from ronin.safety.policy import PolicyEngine, builtin_ruleset
from ronin.tools.base import ToolContext
from ronin.tools.files import ReadTool
from ronin.tools.registry import ToolRegistry

BODY = b"def load_config():\n    return {'retries': 3}\n"


def build(inner: RecordingRegistry, files: FileStateTracker) -> GatedRegistry:
    taint = TaintTracker()
    return gated(inner, PolicyEngine(builtin_ruleset(), taint=taint), files=files, taint=taint)


def reader(content: str = "1\tdef load_config():") -> RecordingRegistry:
    return RecordingRegistry({"read": ToolResult(ok=True, content=content)})


# --------------------------------------------------------------------------- #
# the dedup itself
# --------------------------------------------------------------------------- #


async def test_a_second_read_of_an_unchanged_file_does_not_re_send_it(tmp_path: Path) -> None:
    target = tmp_path / "config.py"
    target.write_bytes(BODY)
    inner = reader()
    gate = build(inner, FileStateTracker())

    first = await gate.execute(use("read", path=str(target)))
    second = await gate.execute(use("read", call_id="call_2", path=str(target)))

    assert inner.called == ("read",)  # the tool ran once, not twice
    assert first.content != second.content
    assert second.ok
    assert "unchanged since you read it" in second.content
    assert gate.log[-1].stages == (GateStage.DEDUP,)


async def test_the_stub_is_a_fraction_of_the_file_it_replaces(tmp_path: Path) -> None:
    """The whole point, stated as a number rather than as a claim."""
    target = tmp_path / "big.py"
    body = "\n".join(f"line {i} of a module nobody wanted to read twice" for i in range(1, 801))
    target.write_text(body)
    inner = reader(content=body)
    gate = build(inner, FileStateTracker())

    first = await gate.execute(use("read", path=str(target)))
    second = await gate.execute(use("read", call_id="call_2", path=str(target)))

    assert len(first.content) > 30_000
    assert len(second.content) < 400
    assert len(second.content) * 50 < len(first.content)


async def test_the_first_read_is_never_stubbed(tmp_path: Path) -> None:
    target = tmp_path / "config.py"
    target.write_bytes(BODY)
    inner = reader()
    gate = build(inner, FileStateTracker())

    result = await gate.execute(use("read", path=str(target)))

    assert inner.called == ("read",)
    assert "unchanged since you read it" not in result.content


async def test_a_file_that_changed_on_disk_is_read_again(tmp_path: Path) -> None:
    target = tmp_path / "config.py"
    target.write_bytes(BODY)
    inner = reader()
    gate = build(inner, FileStateTracker())

    await gate.execute(use("read", path=str(target)))
    target.write_bytes(BODY + b"# and a new line\n")
    await gate.execute(use("read", call_id="call_2", path=str(target)))

    assert inner.called == ("read", "read")


async def test_a_deleted_file_is_read_again_rather_than_stubbed(tmp_path: Path) -> None:
    target = tmp_path / "config.py"
    target.write_bytes(BODY)
    inner = reader()
    gate = build(inner, FileStateTracker())

    await gate.execute(use("read", path=str(target)))
    target.unlink()
    await gate.execute(use("read", call_id="call_2", path=str(target)))

    # Answering "unchanged" for a file that is gone would be the worst stub of all.
    assert inner.called == ("read", "read")
    assert GateStage.DEDUP not in gate.log[-1].stages


async def test_only_read_tools_are_deduplicated(tmp_path: Path) -> None:
    target = tmp_path / "config.py"
    target.write_bytes(BODY)
    inner = RecordingRegistry(
        {"read": ToolResult(ok=True, content="x"), "bash": ToolResult(ok=True, content="y")}
    )
    gate = build(inner, FileStateTracker())

    await gate.execute(use("read", path=str(target)))
    await gate.execute(use("bash", call_id="call_2", path=str(target), command="cat config.py"))

    assert inner.called == ("read", "bash")


async def test_an_image_is_never_stubbed(tmp_path: Path) -> None:
    """Its content rides in ``artifacts``, which is not what visibility is measured on."""
    target = tmp_path / "diagram.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    inner = reader(content="[image: diagram.png]")
    gate = build(inner, FileStateTracker())

    await gate.execute(use("read", path=str(target)))
    await gate.execute(use("read", call_id="call_2", path=str(target)))

    assert inner.called == ("read", "read")


# --------------------------------------------------------------------------- #
# the escape hatch
# --------------------------------------------------------------------------- #


async def test_force_re_sends_the_file(tmp_path: Path) -> None:
    target = tmp_path / "config.py"
    target.write_bytes(BODY)
    inner = reader()
    gate = build(inner, FileStateTracker())

    await gate.execute(use("read", path=str(target)))
    forced = await gate.execute(
        use("read", call_id="call_2", path=str(target), **{FORCE_ARGUMENT: True})
    )

    assert inner.called == ("read", "read")
    assert "unchanged since you read it" not in forced.content


async def test_the_stub_names_the_escape_hatch(tmp_path: Path) -> None:
    """An override the model is never told about is not an override."""
    target = tmp_path / "config.py"
    target.write_bytes(BODY)
    gate = build(reader(), FileStateTracker())

    await gate.execute(use("read", path=str(target)))
    stub = await gate.execute(use("read", call_id="call_2", path=str(target)))

    assert f"{FORCE_ARGUMENT}=true" in stub.content
    assert "read again" in stub.content


def test_force_is_in_the_schema_the_model_is_shown() -> None:
    from ronin.tools.files import ReadTool

    assert FORCE_ARGUMENT in ReadTool.schema["properties"]
    assert FORCE_ARGUMENT not in ReadTool.schema["required"]


# --------------------------------------------------------------------------- #
# windows: the stub must never claim lines the model did not see
# --------------------------------------------------------------------------- #


def _windowed(tmp_path: Path) -> tuple[Path, FileStateTracker]:
    target = tmp_path / "long.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 400)))
    return target, FileStateTracker()


def test_a_windowed_read_does_not_satisfy_a_whole_file_read(tmp_path: Path) -> None:
    target, files = _windowed(tmp_path)
    files.record_read(target, window=ReadWindow.of(100, 20))

    assert files.satisfies(target, ReadWindow.of(100, 20))
    assert not files.satisfies(target, WHOLE_FILE)


def test_a_whole_file_read_does_not_satisfy_a_different_window(tmp_path: Path) -> None:
    target, files = _windowed(tmp_path)
    files.record_read(target, window=ReadWindow.of(1, 20))

    assert not files.satisfies(target, ReadWindow.of(200, 20))


def test_offset_one_and_no_offset_are_the_same_window() -> None:
    # Otherwise the two spellings of "from the top" are two records that never agree.
    assert ReadWindow.of(None, None) == ReadWindow.of(1, None) == WHOLE_FILE
    assert ReadWindow.of(0, None) == WHOLE_FILE  # 0 is not a line number


async def test_the_gate_reads_the_window_off_the_call(tmp_path: Path) -> None:
    target = tmp_path / "long.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 400)))
    inner = reader()
    gate = build(inner, FileStateTracker())

    await gate.execute(use("read", path=str(target), offset=100, limit=20))
    await gate.execute(use("read", call_id="call_2", path=str(target), offset=100, limit=20))
    assert len(inner.called) == 1  # same window: stubbed

    await gate.execute(use("read", call_id="call_3", path=str(target)))
    assert len(inner.called) == 2  # whole file: not what was seen


async def test_a_malformed_offset_is_not_treated_as_no_offset(tmp_path: Path) -> None:
    target = tmp_path / "long.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 400)))
    inner = reader()
    gate = build(inner, FileStateTracker())

    await gate.execute(use("read", path=str(target)))
    await gate.execute(use("read", call_id="call_2", path=str(target), offset="banana"))

    # Treating a bad offset as "absent" would silently stub a call that asked for
    # something else. The tool owns that error message — see the fidelity suite,
    # which drives the real tool and checks it actually produces one.
    assert inner.called == ("read", "read")
    assert GateStage.DEDUP not in gate.log[-1].stages


# --------------------------------------------------------------------------- #
# the guard that was lying, and the sync that fixes it
# --------------------------------------------------------------------------- #


async def _folded(tmp_path: Path, *, grep_the_same_path: bool) -> tuple[Path, list[Message]]:
    """A session long enough to fold, optionally with a grep that displaces the read."""
    target = tmp_path / "a.py"
    target.write_bytes(BODY)

    async def summarize(_: str) -> str:
        return "\n".join(
            f"## {s}\n- x" for s in ("Goal", "Files touched", "Learned", "Failed", "Remaining")
        )

    def turn(index: int, tool: str, path: str, content: str) -> list[Message]:
        call = ToolUse(id=f"t{index}", name=tool, arguments={"path": path})
        return [
            Message(role=Role.USER, content_blocks=(Text(f"step {index}"),)),
            Message(role=Role.ASSISTANT, content_blocks=(call,)),
            Message(
                role=Role.TOOL,
                content_blocks=(ToolResultBlock(tool_use_id=f"t{index}", content=content),),
            ),
        ]

    messages = turn(1, "read", str(target), BODY.decode())
    if grep_the_same_path:
        messages += turn(2, "grep", str(target), "a.py:2:    return {'retries': 3}")
    for index in range(3, 30):
        messages += turn(index, "read", str(tmp_path / f"filler{index}.py"), "y" * 400)
    messages += [Message(role=Role.USER, content_blocks=(Text("now edit a.py"),))]

    result = await compact(
        messages, policy=CompactionPolicy(context_window=4000), summarizer=summarize
    )
    assert result.compacted
    return target, list(result.messages)


async def test_a_grep_on_a_read_file_displaces_its_contents(tmp_path: Path) -> None:
    """The mechanism, isolated: retention is per path and last write wins."""
    target, folded = await _folded(tmp_path, grep_the_same_path=True)
    kept = "\n".join(
        block.content
        for message in folded
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock)
    )
    assert BODY.decode().strip() not in kept
    assert str(target) not in paths_with_visible_read(folded, read_tools={"read"})


async def test_without_the_grep_the_read_survives_the_fold(tmp_path: Path) -> None:
    # The control. Without it the test above proves only that compaction drops things.
    target, folded = await _folded(tmp_path, grep_the_same_path=False)
    assert str(target) in paths_with_visible_read(folded, read_tools={"read"})


async def test_the_guard_stops_calling_a_folded_away_file_safe_to_edit(tmp_path: Path) -> None:
    """The bug, and the fix, in one assertion pair."""
    target, folded = await _folded(tmp_path, grep_the_same_path=True)
    files = FileStateTracker()
    files.record_read(target)

    # What the tracker said before it was told anything about the transcript.
    assert files.check(target).status is FileStatus.UNCHANGED
    assert files.check(target).safe_to_edit

    forgotten = files.retain_only(paths_with_visible_read(folded, read_tools={"read"}))

    assert str(target) in forgotten
    assert files.check(target).status is FileStatus.NEVER_READ
    assert not files.check(target).safe_to_edit
    assert "Read it first" in files.check(target).message


async def test_a_folded_away_file_is_read_again_rather_than_stubbed(tmp_path: Path) -> None:
    """Dedup inherits the repair: no record, no stub."""
    target, folded = await _folded(tmp_path, grep_the_same_path=True)
    inner = reader()
    files = FileStateTracker()
    gate = build(inner, files)

    await gate.execute(use("read", path=str(target)))
    await gate.execute(use("read", call_id="call_2", path=str(target)))
    assert len(inner.called) == 1  # stubbed while the content is still there

    gate.sync_file_state(folded)

    await gate.execute(use("read", call_id="call_3", path=str(target)))
    assert len(inner.called) == 2


async def test_sync_resolves_transcript_paths_the_way_records_are_keyed(tmp_path: Path) -> None:
    """The two ends key differently, and a mismatch would forget everything.

    Records are keyed on the resolved absolute path; the transcript holds whatever the
    model typed, which for a real session is usually **relative**. If the sync compared
    them raw, every path would look invisible and every session would lose its
    stale-edit guard at the first compaction — silently, reported as a routine fold.

    Written with a genuinely relative path on purpose. An earlier version of this test
    used an absolute path containing a ``.`` segment, which ``_normalize_path`` already
    collapses before ``sync_file_state`` ever sees it — so it passed with the
    resolution step deleted, which is the one thing it was supposed to prove.
    """
    target = tmp_path / "a.py"
    target.write_bytes(BODY)
    files = FileStateTracker()
    inner = ToolRegistry((ReadTool(),), ToolContext(root=tmp_path))
    taint = TaintTracker()
    gate = gated(inner, PolicyEngine(builtin_ruleset(), taint=taint), files=files, taint=taint)
    await gate.execute(use("read", path=str(target)))
    assert files.recorded(target) is not None

    call = ToolUse(id="t1", name="read", arguments={"path": "a.py"})  # as the model typed it
    visible = [
        Message(role=Role.ASSISTANT, content_blocks=(call,)),
        Message(
            role=Role.TOOL,
            content_blocks=(ToolResultBlock(tool_use_id="t1", content=BODY.decode()),),
        ),
    ]

    assert gate.sync_file_state(visible) == ()
    assert files.recorded(target) is not None


async def test_sync_keeps_everything_when_no_path_resolves(tmp_path: Path) -> None:
    """A broken resolver must not read as "the fold dropped everything"."""
    target = tmp_path / "a.py"
    target.write_bytes(BODY)
    files = FileStateTracker()
    files.record_read(target)

    def refuse(_: str) -> Path:
        raise RuntimeError("resolver is broken")

    taint = TaintTracker()
    gate = gated(
        reader(),
        PolicyEngine(builtin_ruleset(), taint=taint),
        files=files,
        taint=taint,
        resolve=refuse,
    )
    call = ToolUse(id="t1", name="read", arguments={"path": "a.py"})
    messages = [
        Message(role=Role.ASSISTANT, content_blocks=(call,)),
        Message(
            role=Role.TOOL,
            content_blocks=(ToolResultBlock(tool_use_id="t1", content=BODY.decode()),),
        ),
    ]

    with pytest.raises(RuntimeError, match="not one path"):
        gate.sync_file_state(messages)
    assert files.recorded(target) is not None  # nothing forgotten


async def test_sync_keeps_a_path_that_will_not_resolve_while_others_do(tmp_path: Path) -> None:
    """The invariant the docstring states and nothing checked.

    ``sync_file_state`` says: *"A path that will not resolve is kept under both
    spellings rather than dropped. Forgetting on a resolution failure would quietly
    weaken the stale-edit check."* The line that implements it is one ``visible.add``
    of the raw spelling, before the resolve that may throw.

    The neighbouring test covers the *tripwire* — when not one path resolves, the
    resolver is broken and nothing is forgotten. This is the other branch, and the
    dangerous one: when some paths resolve, the tripwire stays silent, so a dropped
    raw spelling means that one file is quietly forgotten while the compaction is
    reported as routine. The next edit of it is then checked against nothing.
    """
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    for path in (good, bad):
        path.write_bytes(BODY)
    files = FileStateTracker()
    files.record_read(good)
    files.record_read(bad)

    def resolve_except_bad(raw: str) -> Path:
        if raw == str(bad):
            raise RuntimeError("this spelling cannot be resolved")
        return Path(raw)

    taint = TaintTracker()
    gate = gated(
        reader(),
        PolicyEngine(builtin_ruleset(), taint=taint),
        files=files,
        taint=taint,
        resolve=resolve_except_bad,
    )
    messages: list[Message] = []
    for i, path in enumerate((good, bad)):
        call = ToolUse(id=f"t{i}", name="read", arguments={"path": str(path)})
        messages.append(Message(role=Role.ASSISTANT, content_blocks=(call,)))
        messages.append(
            Message(
                role=Role.TOOL,
                content_blocks=(ToolResultBlock(tool_use_id=f"t{i}", content=BODY.decode()),),
            )
        )

    dropped = gate.sync_file_state(messages)

    assert dropped == ()  # neither file left the transcript, so neither may be forgotten
    assert files.recorded(good) is not None
    assert files.recorded(bad) is not None, (
        "the unresolvable path was forgotten, so the stale-edit guard no longer "
        "covers it — and the compaction reported nothing unusual"
    )


# --------------------------------------------------------------------------- #
# what counts as "visible"
# --------------------------------------------------------------------------- #


def _couple(name: str, path: str, content: str, *, clamped: bool = False) -> list[Message]:
    metadata = {CLAMPED_KEY: True} if clamped else {}
    call = ToolUse(id="t1", name=name, arguments={"path": path})
    return [
        Message(role=Role.ASSISTANT, content_blocks=(call,), metadata=metadata),
        Message(
            role=Role.TOOL,
            content_blocks=(ToolResultBlock(tool_use_id="t1", content=content),),
            metadata=metadata,
        ),
    ]


def test_a_grep_result_is_not_file_contents() -> None:
    messages = _couple("grep", "a.py", "a.py:2: match")
    assert paths_with_visible_read(messages, read_tools={"read"}) == frozenset()


def test_a_read_whose_result_was_dropped_is_not_visible() -> None:
    # An unanswered call is a call whose content never arrived or did not survive.
    call = ToolUse(id="t1", name="read", arguments={"path": "a.py"})
    messages = [Message(role=Role.ASSISTANT, content_blocks=(call,))]
    assert paths_with_visible_read(messages, read_tools={"read"}) == frozenset()


def test_a_clamped_retained_read_is_not_visible() -> None:
    """Half a file is not the file."""
    whole = _couple("read", "a.py", "the whole thing")
    part = _couple("read", "a.py", "the who…", clamped=True)
    assert paths_with_visible_read(whole, read_tools={"read"}) == frozenset({"a.py"})
    assert paths_with_visible_read(part, read_tools={"read"}) == frozenset()


async def test_max_retained_chars_marks_what_it_cut(tmp_path: Path) -> None:
    """The flag is set where clamping happens, because nothing downstream can tell."""

    async def summarize(_: str) -> str:
        return "\n".join(
            f"## {s}\n- x" for s in ("Goal", "Files touched", "Learned", "Failed", "Remaining")
        )

    def turn(index: int, path: str, content: str) -> list[Message]:
        call = ToolUse(id=f"t{index}", name="read", arguments={"path": path})
        return [
            Message(role=Role.USER, content_blocks=(Text(f"step {index}"),)),
            Message(role=Role.ASSISTANT, content_blocks=(call,)),
            Message(
                role=Role.TOOL,
                content_blocks=(ToolResultBlock(tool_use_id=f"t{index}", content=content),),
            ),
        ]

    messages: list[Message] = []
    for index in range(1, 30):
        messages += turn(index, f"f{index}.py", "z" * 900)
    messages += [Message(role=Role.USER, content_blocks=(Text("done"),))]

    result = await compact(
        messages,
        policy=CompactionPolicy(context_window=40_000, max_retained_chars=100),
        summarizer=summarize,
    )

    clamped = {
        str(m.metadata[RETAINED_PATH_KEY]) for m in result.messages if m.metadata.get(CLAMPED_KEY)
    }
    assert result.retained_paths
    assert clamped, "the policy bound the retained size, so something must have been cut"

    visible = paths_with_visible_read(result.messages, read_tools={"read"})

    # Not "nothing is visible" — the pinned tail is kept verbatim and is genuinely
    # still there. The claim is narrower and is the one that matters: no path whose
    # content was cut down counts as a file the model still holds.
    assert not (clamped & visible)
    assert visible, "the pinned tail should still count as read"


# --------------------------------------------------------------------------- #
# degrading
# --------------------------------------------------------------------------- #


def test_a_gate_with_no_tracker_syncs_to_nothing_rather_than_raising() -> None:
    gate = gated(reader(), PolicyEngine(builtin_ruleset(), taint=TaintTracker()))
    assert gate.sync_file_state([]) == ()


def test_retain_only_reports_what_it_dropped_in_a_stable_order(tmp_path: Path) -> None:
    files = FileStateTracker()
    for name in ("c.py", "a.py", "b.py"):
        path = tmp_path / name
        path.write_bytes(BODY)
        files.record_read(path)

    dropped = files.retain_only([str(tmp_path / "b.py")])

    assert dropped == (str(tmp_path / "a.py"), str(tmp_path / "c.py"))
    assert files.known_paths() == (str(tmp_path / "b.py"),)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
