"""What the record claims, versus what the model was actually shown.

#192 gave every read a digest, a window and a dedup stub built on both. An audit of
that change found the record could claim more than the model ever received, in five
independent ways. Each one ends the same: a repeat read answered with "you already
have it", or an edit told it is "safe to edit against the content you have", when it
is not.

* **The digest was of a second read.** The tool read the file and rendered a window;
  the gate then hashed the file by reading it *again*. A save landing between the two
  is recorded as the version the model never saw — and because the recorded stat
  matches that same version, ``check`` takes its fast path and answers UNCHANGED
  without even re-hashing. ``record_read`` has always accepted the shown bytes for
  exactly this reason; nothing passed them.
* **The gate and the tool disagreed about what a window is.** ``optional_int``
  tolerates the string form models send, so ``offset="300"`` serves forty lines while
  the gate recorded a *whole-file* window.
* **The tool's line cap** and **the gate's output clamp** both hand the model a
  prefix, and neither was recorded.
* **A failed read kept a path "visible"** to compaction, because retention keeps the
  most recent result per path and an error is a result.

The through-line: a record is two claims, not one — *this is what the file hashed to*
and *this is what the model holds*. They were conflated. ``ReadRecord.complete`` is
the separation: a clamped or capped read is still a sound change-detection baseline
and is no longer a stand-in for content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gate_harness import RecordingRegistry, use

from ronin.cli.gate import NO_DEDUP_SUFFIXES, GatedRegistry, gated, resolve_path
from ronin.context.budget import OutputBudget
from ronin.context.compaction import paths_with_visible_read
from ronin.context.filestate import (
    WHOLE_FILE,
    FileStateTracker,
    FileStatus,
    ReadWindow,
    digest_bytes,
)
from ronin.core.types import Message, Role, ToolResult, ToolResultBlock, ToolUse
from ronin.safety.injection import TaintTracker
from ronin.safety.policy import PolicyEngine, builtin_ruleset
from ronin.tools.base import ToolContext
from ronin.tools.files import IMAGE_SUFFIXES, EditTool, ReadTool, WriteTool, _read_bytes_and_text
from ronin.tools.registry import ToolRegistry


def real_gate(
    root: Path, *tools: Any, budget: OutputBudget | None = None
) -> tuple[FileStateTracker, GatedRegistry]:
    """A gate over the *real* tools. The dedup suite drives fakes; these need truth."""
    files, taint = FileStateTracker(), TaintTracker()
    inner = ToolRegistry(tools or (ReadTool(),), ToolContext(root=root))
    gate = gated(
        inner, PolicyEngine(builtin_ruleset(), taint=taint), files=files, taint=taint, budget=budget
    )
    return files, gate


async def read(gate: GatedRegistry, path: Path, **args: Any) -> ToolResult:
    return await gate.execute(use("read", call_id="r1", path=str(path), **args))


# --------------------------------------------------------------------------- #
# the digest is of the bytes the model saw, not of a later re-read
# --------------------------------------------------------------------------- #


class RacingRead(ReadTool):
    """``read``, but the user saves the file the instant after the bytes are shown."""

    hands_over = True

    async def run(self, args: Any, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        raw, text = _read_bytes_and_text(path)
        ctx.mark_read(path, raw if self.hands_over else None)
        path.write_bytes(b"the user's save, landing mid-flight\n")
        return ToolResult(ok=True, content=text)


async def test_the_digest_is_of_what_the_model_saw_not_of_a_re_read(tmp_path: Path) -> None:
    shown = b"TIMEOUT = 30\n"
    target = tmp_path / "config.py"
    target.write_bytes(shown)
    tool = RacingRead()
    tool.hands_over = True
    files, gate = real_gate(tmp_path, tool)

    await read(gate, target)

    record = files.recorded(target)
    assert record is not None
    assert record.digest == digest_bytes(shown)
    # The file on disk has moved on, so the honest verdict is CHANGED.
    assert files.check(target).status is FileStatus.CHANGED
    assert not files.check(target).safe_to_edit


async def test_without_the_handoff_the_guard_calls_a_raced_file_safe_to_edit(
    tmp_path: Path,
) -> None:
    """The control. Without it the test above only proves the happy path still works.

    This is the bug, reproduced: the record describes the file perfectly and describes
    the *wrong version* of it, and because record and stat agree, ``check`` short-
    circuits to UNCHANGED. A ``write`` against that reverts the user's save.
    """
    shown = b"TIMEOUT = 30\n"
    target = tmp_path / "config.py"
    target.write_bytes(shown)
    tool = RacingRead()
    tool.hands_over = False
    files, gate = real_gate(tmp_path, tool)

    await read(gate, target)

    record = files.recorded(target)
    assert record is not None
    assert record.digest != digest_bytes(shown)
    assert files.check(target).status is FileStatus.UNCHANGED
    assert files.check(target).safe_to_edit


def test_mark_read_keeps_raw_bytes_rather_than_a_re_encode(tmp_path: Path) -> None:
    """A latin-1 fallback does not round-trip through UTF-8.

    ``read`` decodes non-UTF-8 files as latin-1 so a stray byte does not refuse the
    file. Recording ``text.encode("utf-8")`` would therefore never match a later
    re-read — turning a rare wrong-in-the-unsafe-direction record into a guaranteed
    wrong-in-the-safe-direction one on every such file, with every edit refused.
    """
    target = tmp_path / "latin.py"
    raw = b"# caf\xe9\nx = 1\n"
    target.write_bytes(raw)

    recovered, text = _read_bytes_and_text(target)

    assert recovered == raw
    assert text.encode("utf-8") != raw  # the re-encode that would have been wrong

    ctx = ToolContext(root=tmp_path)
    ctx.mark_read(target, recovered)
    handoff = ctx.take_read_bytes(target)
    assert handoff == (raw, True)


def test_the_handoff_is_one_slot_and_is_consumed(tmp_path: Path) -> None:
    # A dict that accumulated every file ever read would be a cache, and would serve
    # one file's bytes for another file's record.
    ctx = ToolContext(root=tmp_path)
    ctx.mark_read(tmp_path / "a.py", b"aaa")
    ctx.mark_read(tmp_path / "b.py", b"bbb")

    assert ctx.take_read_bytes(tmp_path / "a.py") is None  # displaced by b
    assert ctx.take_read_bytes(tmp_path / "b.py") == (b"bbb", True)
    assert ctx.take_read_bytes(tmp_path / "b.py") is None  # consumed


async def test_a_tool_that_hands_over_nothing_still_records(tmp_path: Path) -> None:
    """The fallback re-read stays. An MCP file reader knows nothing about this handoff.

    Its digest is of a re-read and so carries the race this change closes for the
    built-in ``read`` — but a record from a second read beats no record at all, and
    the alternative is the stale-edit guard silently not applying to those tools.
    """
    target = tmp_path / "a.py"
    target.write_bytes(b"x = 1\n")
    files = FileStateTracker()
    taint = TaintTracker()
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="x = 1")})
    gate = gated(inner, PolicyEngine(builtin_ruleset(), taint=taint), files=files, taint=taint)

    await gate.execute(use("read", path=str(target)))

    record = files.recorded(target)
    assert record is not None
    assert record.digest == digest_bytes(b"x = 1\n")
    assert record.complete


# --------------------------------------------------------------------------- #
# the recorded window must be the window the tool served
# --------------------------------------------------------------------------- #


async def test_a_numeric_string_window_is_recorded_as_the_window_it_served(
    tmp_path: Path,
) -> None:
    """``optional_int`` accepts ``"300"``; the gate used to reject it and record whole-file."""
    target = tmp_path / "big.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 500)))
    files, gate = real_gate(tmp_path)

    await read(gate, target, offset="300", limit="40")

    record = files.recorded(target)
    assert record is not None
    assert record.window == ReadWindow(offset=300, limit=40)
    # The forty lines it saw are not the file, so a bare re-read must be served.
    assert not files.satisfies(target, WHOLE_FILE)


async def test_an_unparseable_window_records_nothing_rather_than_whole_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "big.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 50)))
    files = FileStateTracker()
    taint = TaintTracker()
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="something")})
    gate = gated(inner, PolicyEngine(builtin_ruleset(), taint=taint), files=files, taint=taint)

    await gate.execute(use("read", path=str(target), offset="banana"))

    # Fabricating WHOLE_FILE here is what let a windowed read be answered later with
    # "the whole file is already above".
    assert files.recorded(target) is None
    assert any("could not parse" in note for note in gate.log[-1].notes)


def test_a_non_positive_limit_is_not_a_window() -> None:
    # Otherwise the stub renders "lines 1-0".
    assert ReadWindow.of(None, 0) == WHOLE_FILE
    assert ReadWindow.of(None, -5) == WHOLE_FILE
    assert ReadWindow.of(1, 20) == ReadWindow(offset=1, limit=20)


# --------------------------------------------------------------------------- #
# a prefix is not the file: the cap and the clamp
# --------------------------------------------------------------------------- #


async def test_a_read_the_tool_capped_is_not_answered_with_a_stub(tmp_path: Path) -> None:
    target = tmp_path / "huge.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 3000)))
    files, gate = real_gate(tmp_path)

    result = await read(gate, target)

    assert "not shown" in result.content  # the cap fired
    record = files.recorded(target)
    assert record is not None and not record.complete
    assert not files.satisfies(target, WHOLE_FILE)


async def test_a_read_the_gate_clamped_is_not_answered_with_a_stub(tmp_path: Path) -> None:
    target = tmp_path / "wide.py"
    target.write_text("\n".join(f"line {i} " + "x" * 80 for i in range(1, 400)))
    files, gate = real_gate(tmp_path, budget=OutputBudget(remaining_chars=600))

    await read(gate, target)

    record = files.recorded(target)
    assert record is not None and not record.complete
    assert not files.satisfies(target, WHOLE_FILE)


async def test_a_complete_small_read_is_still_deduplicated(tmp_path: Path) -> None:
    # The control for both tests above: `complete` must not switch dedup off wholesale.
    target = tmp_path / "small.py"
    target.write_text("x = 1\n")
    files, gate = real_gate(tmp_path)

    await read(gate, target)

    record = files.recorded(target)
    assert record is not None and record.complete
    assert files.satisfies(target, WHOLE_FILE)


# --------------------------------------------------------------------------- #
# what the gate declines to answer, the tool has to answer for real
# --------------------------------------------------------------------------- #


async def test_the_tool_really_does_report_a_malformed_offset(tmp_path: Path) -> None:
    """The half the dedup suite asserts by omission.

    Over there the reasoning is "a bad offset must not be read as *no* offset, or the
    gate stubs a call that asked for something else — the tool owns that error." The
    tool is a fake in that suite, so the second clause was never checked: had ``read``
    quietly ignored the bad value, the gate would have been right to stand aside and
    the model would still have got a whole file it did not ask for.
    """
    target = tmp_path / "long.py"
    target.write_text("\n".join(f"line {i}" for i in range(1, 400)))
    _files, gate = real_gate(tmp_path)

    result = await gate.execute(use("read", call_id="r1", path=str(target), offset="banana"))

    assert not result.ok
    assert "offset" in (result.error or "")
    assert "banana" in (result.error or "")


async def test_a_read_that_failed_is_not_recorded_as_one_that_happened(
    tmp_path: Path,
) -> None:
    """One line in the gate stands between a refusal and the worst stub in the system.

    ``_record_read`` runs under ``if result.ok``. Without it a *failed* read still
    resolves the path, re-reads the file to digest it, and files a whole-file record
    marked complete — for a call that returned no content whatsoever. The second read
    is then satisfied, and the model is told:

        report.pdf is unchanged since you read it, and the whole file is already in
        this conversation above … Scroll back for the contents.

    about a file it was explicitly refused. The refusal it needed to see is gone, and
    the answer arrives as ``ok=True``.

    A PDF is the sharp case because the tool refuses it every time, so there is no
    path by which the model could ever come to hold it. The whole suite passes with
    that guard deleted; this test is the one that does not.
    """
    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 8)
    files, gate = real_gate(tmp_path)

    first = await gate.execute(use("read", call_id="r1", path=str(target)))
    second = await gate.execute(use("read", call_id="r2", path=str(target)))

    assert not first.ok
    assert files.recorded(target) is None
    # The second call must get the same refusal, not a stub standing in for it.
    assert not second.ok
    assert second.error == first.error


async def test_a_read_that_fails_after_a_good_one_leaves_the_first_record_alone(
    tmp_path: Path,
) -> None:
    """Deleting the file is the case where forgetting would be the wrong repair.

    ``check`` answers ``DELETED`` — "the file you read is gone" — only while the
    record survives. Dropping it on the failed read would downgrade that to
    ``NEVER_READ``, which reads as *you have not looked at this yet* and is a strictly
    less useful thing to tell a model about a file it did look at.
    """
    target = tmp_path / "config.py"
    target.write_text("x = 1\n")
    files, gate = real_gate(tmp_path)

    await read(gate, target)
    target.unlink()
    second = await gate.execute(use("read", call_id="r2", path=str(target)))

    assert not second.ok
    assert "does not exist" in (second.error or "")
    assert files.recorded(target) is not None
    assert files.check(target).status is FileStatus.DELETED


# --------------------------------------------------------------------------- #
# an empty file has nothing to scroll back to
# --------------------------------------------------------------------------- #


async def test_a_second_read_of_an_empty_file_is_answered_with_the_file(
    tmp_path: Path,
) -> None:
    """The dedup spent more context than it saved, and lied while doing it.

    ``read`` of an empty file returns a *description* — "notes.txt exists but is
    empty (0 bytes)" — and used to record it as though it were the contents. So the
    second read was answered with the stub: 282 characters telling the model to
    scroll back for contents that are 40 characters of "there are none". Seven times
    the cost, in the mechanism whose only justification is cost, pointing the model
    at something it cannot find.
    """
    target = tmp_path / "notes.txt"
    target.write_text("")
    _files, gate = real_gate(tmp_path)

    first = await read(gate, target)
    second = await gate.execute(use("read", call_id="r2", path=str(target)))

    assert first.content == second.content
    assert "0 bytes" in second.content
    assert "scroll back" not in second.content.lower()
    assert len(second.content) <= len(first.content)


async def test_an_empty_read_still_records_enough_to_check_a_later_write(
    tmp_path: Path,
) -> None:
    """Withholding the handoff must not cost the guard.

    ``complete`` gates the dedup and nothing else — the digest is still recorded, so
    a write after reading an empty file is still a write the model has looked at.
    Getting this wrong would trade a cosmetic waste for a refused legitimate write.
    """
    target = tmp_path / "notes.txt"
    target.write_text("")
    files, gate = real_gate(tmp_path, ReadTool(), WriteTool())

    await read(gate, target)
    written = await gate.execute(
        use("write", call_id="w1", path=str(target), content="first line\n")
    )

    assert written.ok, written.error
    assert target.read_text() == "first line\n"
    assert files.recorded(target) is not None


async def test_a_file_that_becomes_empty_is_read_again_not_stubbed(tmp_path: Path) -> None:
    """The path that gets there by truncation, not by starting out that way.

    The first read is a real, complete copy of the contents. Emptying the file makes
    the stub actively wrong — and the record must not survive the change in a form
    that still satisfies the dedup.
    """
    target = tmp_path / "notes.txt"
    target.write_text("x = 1\n")
    files, gate = real_gate(tmp_path)

    await read(gate, target)
    assert files.satisfies(target, WHOLE_FILE)  # the ordinary case still holds
    target.write_text("")
    second = await gate.execute(use("read", call_id="r2", path=str(target)))

    assert "0 bytes" in second.content
    assert not files.satisfies(target, WHOLE_FILE)


# --------------------------------------------------------------------------- #
# the model's own edits are not the user's
# --------------------------------------------------------------------------- #


async def test_two_edits_in_a_row_are_allowed(tmp_path: Path) -> None:
    """The guard used to blame the user for the model's own write.

    ``read`` records D1; ``edit`` writes D2 but only updated ``ToolContext``; the
    second ``edit`` saw CHANGED and was refused with "the user or another process
    edited it". Nobody had.
    """
    target = tmp_path / "a.py"
    target.write_text("one = 1\ntwo = 2\n")
    files, gate = real_gate(tmp_path, ReadTool(), EditTool(), WriteTool())

    await read(gate, target)
    first = await gate.execute(
        use("edit", call_id="e1", path=str(target), old_string="one = 1", new_string="one = 11")
    )
    second = await gate.execute(
        use("edit", call_id="e2", path=str(target), old_string="two = 2", new_string="two = 22")
    )

    assert first.ok, first.error
    assert second.ok, second.error
    assert target.read_text() == "one = 11\ntwo = 22\n"
    # And the baseline tracks the file the model just wrote, not the one it read.
    assert files.check(target).status is FileStatus.UNCHANGED


async def test_an_edit_re_baselines_but_does_not_make_the_file_stubbable(
    tmp_path: Path,
) -> None:
    """After an edit the model holds the file it read plus an intention, not the bytes."""
    target = tmp_path / "a.py"
    target.write_text("one = 1\n")
    files, gate = real_gate(tmp_path, ReadTool(), EditTool())

    await read(gate, target)
    await gate.execute(
        use("edit", call_id="e1", path=str(target), old_string="one = 1", new_string="one = 11")
    )

    assert files.check(target).status is FileStatus.UNCHANGED  # baseline is current
    assert not files.satisfies(target, WHOLE_FILE)  # but not a content stand-in


# --------------------------------------------------------------------------- #
# compaction: an error is not content
# --------------------------------------------------------------------------- #


def _couple(name: str, path: str, content: str, *, is_error: bool = False) -> list[Message]:
    call = ToolUse(id="t1", name=name, arguments={"path": path})
    return [
        Message(role=Role.ASSISTANT, content_blocks=(call,)),
        Message(
            role=Role.TOOL,
            content_blocks=(ToolResultBlock(tool_use_id="t1", content=content, is_error=is_error),),
        ),
    ]


def test_a_failed_read_does_not_count_as_the_file_being_visible() -> None:
    ok = _couple("read", "a.py", "the file")
    failed = _couple("read", "a.py", "offset 9000 is past the end", is_error=True)

    assert paths_with_visible_read(ok, read_tools={"read"}) == frozenset({"a.py"})
    assert paths_with_visible_read(failed, read_tools={"read"}) == frozenset()


# --------------------------------------------------------------------------- #
# the two registries of "the model has seen this file" must agree
# --------------------------------------------------------------------------- #


async def test_a_fold_that_forgets_a_read_also_stops_write_overwriting_it(
    tmp_path: Path,
) -> None:
    """The destructive case, and the whole reason `write` has a read-before rule.

    Two registries answer "has the model seen this file": the tracker, which the gate
    prunes when a read leaves the transcript, and ``ToolContext.read_files``, a bare
    set of paths that ``write`` consults. Only the first was pruned — so the fold
    removed the read, the tracker forgot it, and ``write`` still saw the path and
    replaced the whole file with content chosen without looking at it.
    """
    target = tmp_path / "a.py"
    target.write_text("original = 1\n")
    files, gate = real_gate(tmp_path, ReadTool(), WriteTool())

    await read(gate, target)
    assert files.recorded(target) is not None

    dropped = gate.sync_file_state([])  # the read is no longer anywhere in the transcript
    assert str(target) in dropped

    result = await gate.execute(
        use("write", call_id="w1", path=str(target), content="clobbered = 2\n")
    )

    assert not result.ok
    assert target.read_text() == "original = 1\n", "the user's file survived"


async def test_a_restore_also_clears_the_write_guard(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("original = 1\n")
    files, gate = real_gate(tmp_path, ReadTool(), WriteTool())

    await read(gate, target)
    gate.forget_file_state()
    assert files.known_paths() == ()

    result = await gate.execute(
        use("write", call_id="w1", path=str(target), content="clobbered = 2\n")
    )

    assert not result.ok
    assert target.read_text() == "original = 1\n"


async def test_pruning_takes_only_what_the_tracker_dropped(tmp_path: Path) -> None:
    """Not a wholesale clear. Subagents run ungated, so their reads are in
    ``read_files`` and never in the tracker; clearing everything would refuse writes
    to files this layer has no opinion about."""
    tracked = tmp_path / "tracked.py"
    tracked.write_text("a = 1\n")
    untracked = tmp_path / "untracked.py"
    untracked.write_text("b = 2\n")
    _files, gate = real_gate(tmp_path, ReadTool(), WriteTool())
    ctx = gate.inner.ctx  # type: ignore[attr-defined]

    await read(gate, tracked)
    ctx.mark_read(untracked)  # as an ungated subagent read would leave it

    gate.forget_file_state()

    assert not ctx.has_been_read(tracked)
    assert ctx.has_been_read(untracked), "a path the gate never recorded is left alone"


async def test_a_read_that_survives_the_fold_can_still_be_written(tmp_path: Path) -> None:
    # The control: pruning must not switch the write path off wholesale.
    target = tmp_path / "a.py"
    target.write_text("original = 1\n")
    _files, gate = real_gate(tmp_path, ReadTool(), WriteTool())

    await read(gate, target)
    call = ToolUse(id="t1", name="read", arguments={"path": str(target)})
    still_visible = [
        Message(role=Role.ASSISTANT, content_blocks=(call,)),
        Message(
            role=Role.TOOL,
            content_blocks=(ToolResultBlock(tool_use_id="t1", content="original = 1"),),
        ),
    ]
    assert gate.sync_file_state(still_visible) == ()

    result = await gate.execute(
        use("write", call_id="w1", path=str(target), content="deliberate = 2\n")
    )

    assert result.ok, result.error
    assert target.read_text() == "deliberate = 2\n"


# --------------------------------------------------------------------------- #
# wiring that must not drift
# --------------------------------------------------------------------------- #


def test_the_gate_and_the_tool_share_one_definition_of_an_image() -> None:
    # Two hand-copied frozensets would let `.svg` be added to the tool's list — the
    # natural place — and silently make image reads stubbable.
    assert NO_DEDUP_SUFFIXES is IMAGE_SUFFIXES


def test_the_gate_resolves_paths_the_way_its_tools_do(tmp_path: Path) -> None:
    """A gate keyed differently from its tools tracks a different file than is read."""
    ctx = ToolContext(root=tmp_path)
    inner = ToolRegistry((ReadTool(),), ctx)
    taint = TaintTracker()
    gate = gated(inner, PolicyEngine(builtin_ruleset(), taint=taint), taint=taint)

    assert gate.resolve == ctx.resolve


def test_a_registry_with_no_context_still_gets_a_resolver() -> None:
    taint = TaintTracker()
    gate = gated(RecordingRegistry(), PolicyEngine(builtin_ruleset(), taint=taint), taint=taint)

    assert gate.resolve is resolve_path


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
