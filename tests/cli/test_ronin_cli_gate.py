"""The gate: hooks that can say no, stale-edit refusal, and the taint rule that matters.

The suite is organised around the one claim the gate exists to make good on: *any tool
call whose arguments derive from freshly fetched untrusted content escalates to ask,
regardless of the allowlist.* That claim is asserted end to end against the real
:class:`~ronin.safety.policy.PolicyEngine` — a fake engine could agree with anything —
and its mirror image is asserted too, because a gate that prompts on a bare identifier
the model would have typed anyway is a gate people switch off.

Everything here is offline and touches no real file outside ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from gate_harness import (
    BASH,
    COPIED_SPAN,
    FETCHED_PAGE,
    MCP_ISSUE,
    READ,
    SHORT_OVERLAP,
    RecordingAsker,
    RecordingRegistry,
    ScriptedHookProcess,
    ScriptedModel,
    blocked,
    calls,
    engine,
    hook_spec,
    long_output,
    runner,
    says,
    timed_out,
    use,
)

from ronin.agents.hooks import HookCompletion, HookEvent, HookRunner
from ronin.cli.gate import (
    FENCE_REPAIR_NOTE,
    GatedRegistry,
    GateRecord,
    GateStage,
    gated,
    untrusted_source,
)
from ronin.context.budget import ClampMode, OutputBudget
from ronin.context.filestate import FileStateTracker, FileStatus
from ronin.core.loop import run_turn
from ronin.core.protocols import ToolRegistry
from ronin.core.types import (
    AgentState,
    Message,
    Role,
    Text,
    ToolEnd,
    ToolResult,
    ToolUse,
    TurnEnd,
    TurnState,
)
from ronin.safety.injection import CLOSE_MARKER, OPEN_MARKER, TaintTracker
from ronin.safety.policy import Decision, PolicyEngine, builtin_ruleset


def build(
    inner: RecordingRegistry,
    *,
    hooks: HookRunner | None = None,
    files: FileStateTracker | None = None,
    budget: OutputBudget | None = None,
    asker: RecordingAsker | None = None,
) -> tuple[GatedRegistry, TaintTracker]:
    """A gate on the real safety objects, sharing one taint tracker with the engine."""
    taint = TaintTracker()
    gate = gated(
        inner,
        engine(taint, asker=asker),
        hooks=hooks,
        files=files,
        taint=taint,
        budget=budget,
    )
    return gate, taint


# --------------------------------------------------------------------------- #
# The protocol surface
# --------------------------------------------------------------------------- #


def test_specs_and_get_are_the_inner_registrys_unchanged() -> None:
    inner = RecordingRegistry()
    gate, _ = build(inner)
    assert gate.specs() == inner.specs()
    assert gate.get("read") == READ
    assert gate.get("nonexistent") is None


def test_the_gate_is_indistinguishable_from_a_registry_to_the_loop() -> None:
    gate, _ = build(RecordingRegistry())
    # Three methods in, three methods out: `run_turn` is written against the protocol
    # and must not be able to tell it is holding a gate.
    assert isinstance(gate, ToolRegistry)


# --------------------------------------------------------------------------- #
# 1. PreToolUse hooks
# --------------------------------------------------------------------------- #


async def test_a_pre_tool_use_hook_exiting_2_blocks_the_call_and_the_tool_never_runs() -> None:
    process = ScriptedHookProcess({"guard": blocked("migrations/ is off limits")})
    inner = RecordingRegistry()
    gate, _ = build(
        inner, hooks=runner(hook_spec("guard", name="no-migrations"), process=process)
    )

    result = await gate.execute(use("write", path="migrations/001.sql", content="x"))

    assert result.ok is False
    assert "migrations/ is off limits" in result.error
    assert "no-migrations" in result.error
    assert inner.calls == []
    record = gate.log[-1]
    assert record.blocked_by is GateStage.PRE_HOOKS
    assert GateStage.EXECUTE not in record.stages


async def test_a_hook_exiting_0_does_not_block_and_its_stdout_reaches_the_model() -> None:
    process = ScriptedHookProcess(
        {"brief": HookCompletion(exit_code=0, stdout="deps are pinned in uv.lock")}
    )
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="the file")})
    gate, _ = build(inner, hooks=runner(hook_spec("brief"), process=process))

    result = await gate.execute(use("read", path="a.py"))

    assert result.ok is True
    assert "deps are pinned in uv.lock" in result.content
    assert "the file" in result.content
    assert inner.called == ("read",)
    assert gate.log[-1].stages[0] is GateStage.PRE_HOOKS


async def test_a_hook_exiting_1_is_a_recorded_warning_and_the_call_proceeds() -> None:
    process = ScriptedHookProcess(
        {"lint": HookCompletion(exit_code=1, stderr="prettier is not installed")}
    )
    inner = RecordingRegistry()
    gate, _ = build(inner, hooks=runner(hook_spec("lint", name="fmt"), process=process))

    result = await gate.execute(use("read", path="a.py"))

    assert result.ok is True
    assert inner.called == ("read",)
    assert gate.log[-1].hook_warnings == ("fmt: prettier is not installed",)
    # A warning is for the human, not an instruction the model should act on.
    assert "prettier" not in result.content


@pytest.mark.parametrize(
    ("block_on_timeout", "expect_blocked"),
    [(True, True), (False, False)],
    ids=["fails-closed-when-asked", "fails-open-by-default"],
)
async def test_a_timed_out_hook_blocks_only_when_it_asked_to(
    block_on_timeout: bool, expect_blocked: bool
) -> None:
    process = ScriptedHookProcess({"slow": timed_out()})
    inner = RecordingRegistry()
    gate, _ = build(
        inner,
        hooks=runner(
            hook_spec("slow", name="policy-check", block_on_timeout=block_on_timeout),
            process=process,
        ),
    )

    result = await gate.execute(use("write", path="a.py", content="x"))

    assert result.ok is not expect_blocked
    assert (inner.calls == []) is expect_blocked
    if expect_blocked:
        assert "timed out" in result.error
        assert "policy-check" in result.error


async def test_a_hook_matcher_that_does_not_match_leaves_no_stage() -> None:
    process = ScriptedHookProcess({"guard": blocked("never fires")})
    inner = RecordingRegistry()
    gate, _ = build(
        inner, hooks=runner(hook_spec("guard", matcher="write|edit"), process=process)
    )

    result = await gate.execute(use("read", path="a.py"))

    assert result.ok is True
    assert process.invocations == []
    assert gate.log[-1].stages == (GateStage.EXECUTE,)


async def test_a_crashing_pre_tool_use_hook_refuses_the_call() -> None:
    process = ScriptedHookProcess(crash=True)
    inner = RecordingRegistry()
    gate, _ = build(inner, hooks=runner(hook_spec("guard"), process=process))

    result = await gate.execute(use("write", path="a.py", content="x"))

    # Fails closed: pre-conditions that could not be evaluated are not a licence to
    # mutate the tree. A hook *exiting* non-zero is the opposite case, tested above.
    assert result.ok is False
    assert "PreToolUse hooks could not be run" in result.error
    assert "OSError" in result.error
    assert inner.calls == []


async def test_a_crashing_post_tool_use_hook_does_not_undo_a_successful_call() -> None:
    process = ScriptedHookProcess(crash=True)
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="the file")})
    gate, _ = build(
        inner,
        hooks=runner(hook_spec("fmt", event=HookEvent.POST_TOOL_USE), process=process),
    )

    result = await gate.execute(use("read", path="a.py"))

    assert result.ok is True
    assert result.content == "the file"
    assert any("PostToolUse hooks did not run" in note for note in gate.log[-1].notes)


async def test_post_tool_use_stdout_lands_after_the_tools_own_output() -> None:
    process = ScriptedHookProcess(
        {
            "before": HookCompletion(exit_code=0, stdout="on branch main"),
            "after": HookCompletion(exit_code=0, stdout="reformatted 1 file"),
        }
    )
    inner = RecordingRegistry({"write": ToolResult(ok=True, content="wrote a.py")})
    gate, _ = build(
        inner,
        hooks=runner(
            hook_spec("before"),
            hook_spec("after", event=HookEvent.POST_TOOL_USE),
            process=process,
        ),
    )

    result = await gate.execute(use("write", path="a.py", content="x"))

    assert result.content.index("on branch main") < result.content.index("wrote a.py")
    assert result.content.index("wrote a.py") < result.content.index("reformatted 1 file")


async def test_a_post_tool_use_hook_exiting_2_cannot_block_after_the_fact() -> None:
    process = ScriptedHookProcess({"late": blocked("too late")})
    inner = RecordingRegistry({"write": ToolResult(ok=True, content="wrote a.py")})
    gate, _ = build(
        inner,
        hooks=runner(hook_spec("late", event=HookEvent.POST_TOOL_USE), process=process),
    )

    result = await gate.execute(use("write", path="a.py", content="x"))

    assert result.ok is True
    assert gate.log[-1].hook_warnings != ()


# --------------------------------------------------------------------------- #
# 2. File state
# --------------------------------------------------------------------------- #


async def test_an_edit_after_the_file_changed_on_disk_is_refused_with_a_reread_instruction(
    tmp_path: Path,
) -> None:
    target = tmp_path / "config.py"
    target.write_bytes(b"TIMEOUT = 30\n")
    files = FileStateTracker()
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="TIMEOUT = 30")})
    gate, _ = build(inner, files=files)

    await gate.execute(use("read", path=str(target)))
    # The user fixes a typo in their editor while the model is thinking.
    target.write_bytes(b"TIMEOUT = 30  # seconds, not ms\n")

    result = await gate.execute(
        use("edit", call_id="call_2", path=str(target), old_string="30", new_string="60")
    )

    assert result.ok is False
    assert "changed on disk" in result.error
    assert "again first" in result.error
    assert inner.called == ("read",)
    # Bytes, not text: a CRLF-only rewrite must not read as "unchanged" either.
    assert target.read_bytes() == b"TIMEOUT = 30  # seconds, not ms\n"
    record = gate.log[-1]
    assert record.blocked_by is GateStage.FILE_STATE
    assert record.file_status is FileStatus.CHANGED


async def test_an_edit_after_an_unchanged_read_is_allowed(tmp_path: Path) -> None:
    target = tmp_path / "config.py"
    target.write_bytes(b"TIMEOUT = 30\n")
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="TIMEOUT = 30")})
    gate, _ = build(inner, files=FileStateTracker())

    await gate.execute(use("read", path=str(target)))
    result = await gate.execute(use("edit", call_id="call_2", path=str(target)))

    assert result.ok is True
    assert inner.called == ("read", "edit")
    assert gate.log[-1].file_status is FileStatus.UNCHANGED


async def test_an_edit_of_a_file_never_read_is_recorded_but_not_refused_here(
    tmp_path: Path,
) -> None:
    inner = RecordingRegistry()
    gate, _ = build(inner, files=FileStateTracker())

    result = await gate.execute(use("write", path=str(tmp_path / "new.py"), content="x"))

    # `write` refusing to clobber an unread file is the write tool's own rule, where
    # it can tell "new file" from "unread file". The gate only reports the status.
    assert result.ok is True
    assert gate.log[-1].file_status is FileStatus.NEVER_READ


async def test_a_mutating_call_with_no_path_argument_is_refused_as_a_value() -> None:
    inner = RecordingRegistry()
    gate, _ = build(inner, files=FileStateTracker())

    result = await gate.execute(use("edit", old_string="a", new_string="b"))

    assert result.ok is False
    assert "'path'" in result.error
    assert inner.calls == []


async def test_a_path_the_resolver_rejects_refuses_rather_than_skipping_the_check() -> None:
    inner = RecordingRegistry()
    taint = TaintTracker()

    def hostile(path: str) -> Path:
        raise ValueError(f"{path!r} resolves outside the working directory")

    gate = GatedRegistry(
        inner=inner,
        policy=engine(taint),
        files=FileStateTracker(),
        taint=taint,
        resolve=hostile,
    )

    result = await gate.execute(use("write", path="../../etc/hosts", content="x"))

    assert result.ok is False
    assert "outside the working directory" in result.error
    assert inner.calls == []


async def test_a_read_the_gate_cannot_record_degrades_to_a_note(tmp_path: Path) -> None:
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="…")})
    gate, _ = build(inner, files=FileStateTracker())

    # The tool "succeeded" but the path is gone by the time the gate stats it.
    result = await gate.execute(use("read", path=str(tmp_path / "vanished.py")))

    assert result.ok is True
    assert GateStage.RECORD_READ not in gate.log[-1].stages
    assert any("was not recorded" in note for note in gate.log[-1].notes)


# --------------------------------------------------------------------------- #
# 4. Untrusted content: fencing, flagging, and the escalation
# --------------------------------------------------------------------------- #


async def test_fetched_content_is_fenced_and_its_injection_attempt_is_flagged_and_present() -> None:
    inner = RecordingRegistry({"web_fetch": ToolResult(ok=True, content=FETCHED_PAGE)})
    gate, _ = build(inner)

    result = await gate.execute(
        use("web_fetch", url="https://widget.example/notes", prompt="what changed?")
    )

    assert result.ok is True
    assert result.content.startswith(OPEN_MARKER)
    assert result.content.rstrip().endswith(CLOSE_MARKER)
    assert "https://widget.example/notes" in result.content
    assert "INJECTION ATTEMPT FLAGGED" in result.content
    # Flagged and still there, byte for byte: stripping hides the attack from the one
    # person who needs to see it, and mangles documents that merely discuss attacks.
    assert FETCHED_PAGE in result.content
    assert gate.log[-1].findings != ()


async def test_a_call_quoting_fetched_content_is_escalated_to_ask_by_the_real_engine() -> None:
    asker = RecordingAsker()
    inner = RecordingRegistry({"web_fetch": ToolResult(ok=True, content=FETCHED_PAGE)})
    gate, taint = build(inner, asker=asker)

    await gate.execute(use("web_fetch", url="https://widget.example/notes", prompt="?"))

    # `echo …` is on the shipped allowlist, so without taint this is a plain allow.
    quoted = ToolUse(
        id="call_2", name="bash", arguments={"command": f'echo "{COPIED_SPAN}"'}
    )
    verdict = gate.policy.evaluate(BASH, quoted)
    decision = await gate.policy.approve(BASH, quoted, rendered="")

    assert taint.sources == ("https://widget.example/notes",)
    assert verdict.decision is Decision.ASK
    assert verdict.taint is not None
    assert "copied from untrusted content" in verdict.taint.describe()
    # "Escalated to ask", observed the only way that matters: a human was asked.
    assert len(asker.asked) == 1
    assert decision.approved is False


async def test_a_short_overlap_with_fetched_content_is_not_escalated() -> None:
    asker = RecordingAsker()
    inner = RecordingRegistry({"web_fetch": ToolResult(ok=True, content=FETCHED_PAGE)})
    gate, _ = build(inner, asker=asker)

    await gate.execute(use("web_fetch", url="https://widget.example/notes", prompt="?"))

    typed = ToolUse(
        id="call_2",
        name="bash",
        arguments={"command": f"echo checking {SHORT_OVERLAP} now for the defaults"},
    )
    verdict = gate.policy.evaluate(BASH, typed)
    decision = await gate.policy.approve(BASH, typed, rendered="")

    assert verdict.decision is Decision.ALLOW
    assert verdict.taint is None
    assert asker.asked == []
    assert decision.approved is True


async def test_an_mcp_tools_output_is_untrusted_because_a_server_is_a_third_party() -> None:
    inner = RecordingRegistry({MCP_ISSUE.name: ToolResult(ok=True, content=FETCHED_PAGE)})
    gate, taint = build(inner)

    result = await gate.execute(use(MCP_ISSUE.name, id="ENG-1"))

    assert OPEN_MARKER in result.content
    assert taint.sources == (MCP_ISSUE.name,)
    assert gate.log[-1].taint_source == MCP_ISSUE.name


async def test_a_workspace_read_is_not_treated_as_untrusted_content() -> None:
    inner = RecordingRegistry({"read": ToolResult(ok=True, content=FETCHED_PAGE)})
    gate, taint = build(inner)

    result = await gate.execute(use("read", path="notes.md"))

    # The user's own files are the material the agent was hired to work on. Tainting
    # them would prompt on everything, and a gate that prompts on everything is off.
    assert OPEN_MARKER not in result.content
    assert taint.sources == ()
    assert GateStage.TAINT not in gate.log[-1].stages


async def test_a_widened_untrusted_set_taints_the_tool_it_names() -> None:
    inner = RecordingRegistry({"bash": ToolResult(ok=True, content=FETCHED_PAGE)})
    taint = TaintTracker()
    gate = gated(
        inner,
        engine(taint),
        taint=taint,
        untrusted_tools=frozenset({"bash"}),
    )

    result = await gate.execute(use("bash", command="curl https://widget.example/notes"))

    assert OPEN_MARKER in result.content
    assert taint.sources == ("bash",)


def test_the_source_names_a_url_bare_and_qualifies_everything_else() -> None:
    assert untrusted_source(use("web_fetch", url="https://x.test/a")) == "https://x.test/a"
    assert untrusted_source(use("web_search", query="ripgrep")) == "web_search query=ripgrep"
    assert untrusted_source(use("mcp__tracker__get_issue", id="ENG-1")) == (
        "mcp__tracker__get_issue"
    )


# --------------------------------------------------------------------------- #
# 5. Clamping
# --------------------------------------------------------------------------- #


async def test_oversized_output_is_clamped_with_a_marker_naming_the_true_elided_count() -> None:
    text = long_output(200)
    inner = RecordingRegistry({"read": ToolResult(ok=True, content=text)})
    gate, _ = build(inner, budget=OutputBudget(remaining_chars=600))

    result = await gate.execute(use("read", path="big.log"))

    match = re.search(r"\[\.\.\. (\d+) lines elided \.\.\.\]", result.content)
    assert match is not None, result.content
    claimed = int(match.group(1))
    kept = len([line for line in result.content.splitlines() if line.startswith("line ")])
    assert claimed == 200 - kept
    record = gate.log[-1]
    assert record.clamp_mode is ClampMode.LINES
    assert record.elided_lines == claimed
    # Head and tail, so the start and the end of the output both survive.
    assert result.content.startswith("line 0000")
    assert result.content.rstrip().endswith("x")


async def test_output_that_fits_is_passed_through_byte_for_byte() -> None:
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="short\n")})
    gate, _ = build(inner, budget=OutputBudget(remaining_chars=1_000))

    result = await gate.execute(use("read", path="a.py"))

    assert result.content == "short\n"
    assert GateStage.CLAMP not in gate.log[-1].stages


async def test_a_clamp_that_cuts_the_fence_puts_it_back() -> None:
    # A budget too small to keep the header line drives the head to zero, and the
    # result is untrusted content with a dangling close marker and no open marker —
    # content outside the block the standing instruction talks about.
    page = "x" * 400 + "\n" + "tail line\n" * 5
    inner = RecordingRegistry({"web_fetch": ToolResult(ok=True, content=page)})
    gate, _ = build(inner, budget=OutputBudget(remaining_chars=90))

    result = await gate.execute(use("web_fetch", url="https://x.test/p", prompt="?"))

    assert OPEN_MARKER in result.content
    assert CLOSE_MARKER in result.content
    assert FENCE_REPAIR_NOTE in result.content
    assert gate.log[-1].clamp_mode is not ClampMode.NONE


# --------------------------------------------------------------------------- #
# 7. Ordering
# --------------------------------------------------------------------------- #


async def test_the_record_names_every_stage_in_the_order_it_ran() -> None:
    process = ScriptedHookProcess({"before": HookCompletion(exit_code=0, stdout="ok")})
    inner = RecordingRegistry({"web_fetch": ToolResult(ok=True, content=long_output(200))})
    gate, _ = build(
        inner,
        hooks=runner(
            hook_spec("before"),
            hook_spec("before", event=HookEvent.POST_TOOL_USE),
            process=process,
        ),
        budget=OutputBudget(remaining_chars=600),
    )

    await gate.execute(use("web_fetch", url="https://x.test/p", prompt="?"))

    stages = gate.log[-1].stages
    assert stages == (
        GateStage.PRE_HOOKS,
        GateStage.EXECUTE,
        GateStage.TAINT,
        GateStage.CLAMP,
        GateStage.POST_HOOKS,
    )
    # Spelled out separately because this is the ordering that matters: registering
    # after the clamp would leave the elided middle of the page unregistered, and a
    # page could then hide its payload exactly where the truncator cuts.
    assert stages.index(GateStage.TAINT) < stages.index(GateStage.CLAMP)


async def test_a_read_records_the_file_before_anything_could_clamp_it(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"one\ntwo\n")
    inner = RecordingRegistry({"read": ToolResult(ok=True, content=long_output(200))})
    gate, _ = build(inner, files=FileStateTracker(), budget=OutputBudget(remaining_chars=600))

    await gate.execute(use("read", path=str(target)))

    assert gate.log[-1].stages == (
        GateStage.EXECUTE,
        GateStage.RECORD_READ,
        GateStage.CLAMP,
    )


async def test_the_record_renders_one_line_naming_what_the_gate_did() -> None:
    process = ScriptedHookProcess({"before": HookCompletion(exit_code=0, stdout="ok")})
    inner = RecordingRegistry({"web_fetch": ToolResult(ok=True, content=FETCHED_PAGE)})
    gate, _ = build(inner, hooks=runner(hook_spec("before"), process=process))

    await gate.execute(use("web_fetch", url="https://widget.example/notes", prompt="?"))

    line = gate.log[-1].line()
    assert "web_fetch[call_1]" in line
    assert "pre_hooks → execute → taint" in line
    assert "registered" in line
    assert "injection pattern(s) flagged" in line


def test_an_empty_record_still_renders() -> None:
    assert GateRecord(tool="read", tool_use_id="call_1").line() == "read[call_1]  refused"


# --------------------------------------------------------------------------- #
# Errors are values
# --------------------------------------------------------------------------- #


async def test_a_tool_that_raises_becomes_a_tool_result() -> None:
    def boom(_: ToolUse) -> ToolResult:
        raise RuntimeError("the socket went away")

    inner = RecordingRegistry({"read": boom})
    gate, _ = build(inner)

    result = await gate.execute(use("read", path="a.py"))

    assert result.ok is False
    assert "RuntimeError" in result.error
    assert "the socket went away" in result.error
    assert gate.log[-1].ok is False


async def test_cancellation_propagates_and_is_still_recorded() -> None:
    def cancel(_: ToolUse) -> ToolResult:
        raise asyncio.CancelledError

    inner = RecordingRegistry({"read": cancel})
    gate, _ = build(inner)

    with pytest.raises(asyncio.CancelledError):
        await gate.execute(use("read", path="a.py"))

    assert gate.log[-1].error == "cancelled"
    assert gate.log[-1].ok is False


async def test_a_result_the_gate_itself_chokes_on_is_still_a_value() -> None:
    inner = RecordingRegistry({"read": ToolResult(ok=True, content="x")})
    taint = TaintTracker()

    class ExplodingBudget(OutputBudget):
        def fits(self, text: str) -> bool:
            raise ZeroDivisionError("budget arithmetic went wrong")

    gate = GatedRegistry(
        inner=inner,
        policy=engine(taint),
        taint=taint,
        budget=ExplodingBudget(remaining_chars=10),
    )

    result = await gate.execute(use("read", path="a.py"))

    assert result.ok is False
    assert "the tool gate failed unexpectedly" in result.error
    assert "*after* read had already run" in result.error
    assert gate.log[-1].blocked_by is GateStage.GATE


# --------------------------------------------------------------------------- #
# Wiring: the join this module exists to make impossible to get wrong
# --------------------------------------------------------------------------- #


def test_a_gate_and_engine_holding_different_trackers_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="different TaintTracker"):
        gated(RecordingRegistry(), engine(TaintTracker()), taint=TaintTracker())


def test_a_gate_whose_engine_has_no_tracker_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="PolicyEngine has none"):
        gated(
            RecordingRegistry(),
            PolicyEngine(rules=builtin_ruleset()),
            taint=TaintTracker(),
        )


def test_the_gate_adopts_the_engines_tracker_when_it_was_given_none() -> None:
    taint = TaintTracker()
    gate = gated(RecordingRegistry(), engine(taint))
    assert gate.taint is taint


async def test_without_a_tracker_the_gate_says_so_instead_of_pretending() -> None:
    inner = RecordingRegistry({"web_fetch": ToolResult(ok=True, content=FETCHED_PAGE)})
    gate = gated(inner, PolicyEngine(rules=builtin_ruleset()))

    result = await gate.execute(use("web_fetch", url="https://x.test/p", prompt="?"))

    assert OPEN_MARKER in result.content  # still fenced and scanned
    assert any("not registered" in note for note in gate.log[-1].notes)


# --------------------------------------------------------------------------- #
# Integration: the real loop over the gate
# --------------------------------------------------------------------------- #


async def test_the_loop_sees_a_well_formed_transcript_when_a_hook_blocks_a_call() -> None:
    process = ScriptedHookProcess({"guard": blocked("migrations/ is off limits")})
    inner = RecordingRegistry()
    taint = TaintTracker()
    policy = engine(taint)
    gate = gated(
        inner,
        policy,
        hooks=runner(
            hook_spec("guard", matcher="write|edit", name="no-migrations"),
            process=process,
        ),
        files=FileStateTracker(),
        taint=taint,
    )
    model = ScriptedModel(
        [
            calls(use("write", path="migrations/001.sql", content="drop table users")),
            says("I cannot touch migrations/; the project forbids it."),
        ]
    )
    state = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text("add a migration"),)),)
    )

    events = [event async for event in run_turn(state, model, gate, policy)]

    ends = [event for event in events if isinstance(event, ToolEnd)]
    assert len(ends) == 1
    assert ends[0].result.ok is False
    assert "migrations/ is off limits" in ends[0].result.error
    assert inner.calls == []

    final = events[-1]
    assert isinstance(final, TurnEnd)
    assert final.state is TurnState.DONE
    assert final.agent_state is not None
    # The whole point of a value: the transcript is sendable, so the next turn works.
    assert final.agent_state.pairing_errors == ()
    tool_messages = [m for m in final.agent_state.messages if m.role is Role.TOOL]
    assert len(tool_messages) == 1
    block = tool_messages[0].tool_results[0]
    assert block.is_error is True
    assert "no-migrations" in block.content
    assert gate.log[-1].blocked_by is GateStage.PRE_HOOKS
