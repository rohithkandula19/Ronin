"""The wiring: task → a nested run_turn on the fast model.

This is the first test file that touches all three layers at once, and the
assertions are mostly about the five decisions in ``session.py``'s docstring — each
of which is a place where the obvious wiring is subtly wrong and still passes a
naive test.

The model is a scripted fake and the shell/network are absent, so nothing here opens
a socket or spends money.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path

import pytest
from harness import context, use, write_file

from ronin.core.types import (
    ApprovalDecision,
    Budget,
    DangerLevel,
    Message,
    Role,
    Text,
    ToolSpec,
    ToolUse,
)
from ronin.providers import (
    Capabilities,
    Completed,
    FinishReason,
    Ledger,
    ModelRequest,
    ModelSpec,
    Router,
    RouterConfig,
    TextDelta,
    Usage,
)
from ronin.providers import (
    Role as ModelRole,
)
from ronin.providers.base import ModelClient
from ronin.providers.types import ModelDelta
from ronin.session import (
    FORBIDDEN_IN_SUBAGENTS,
    SubagentPolicy,
    SubagentSession,
    build_session,
    build_subagent_runner,
    default_context,
)
from ronin.tools import ToolContext, build_registry
from ronin.tools.task import SubagentType

CAPS = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=False,
    thinking=False,
    max_context=100_000,
    vision=False,
)


class ScriptedModel:
    """Replays a scripted turn per call, recording the request it was given."""

    def __init__(self, script: Sequence[Sequence[ModelDelta]], label: str = "fast") -> None:
        self.script = tuple(script)
        self.label = label
        self.requests: list[ModelRequest] = []

    def capabilities(self) -> Capabilities:
        return CAPS

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        index = len(self.requests)
        self.requests.append(req)
        step = self.script[min(index, len(self.script) - 1)]
        for delta in step:
            yield delta


def says(text: str, *, tokens: int = 100) -> tuple[ModelDelta, ...]:
    """A turn that answers in prose and calls nothing."""
    return (
        TextDelta(text),
        Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
            finish=FinishReason.STOP,
            usage=Usage(input_tokens=tokens, output_tokens=tokens // 2, cost_usd=0.001),
        ),
    )


def says_and_calls(text: str, name: str, **args: object) -> tuple[ModelDelta, ...]:
    """Narrate, then call — which is what models actually do, and the only shape
    that reaches the iteration cap with text already in hand."""
    return (
        TextDelta(text),
        Completed(
            message=Message(
                role=Role.ASSISTANT,
                content_blocks=(
                    Text(text),
                    ToolUse(id=f"c_{name}_{len(text)}", name=name, arguments=args),
                ),
            ),
            finish=FinishReason.TOOL_USE,
            usage=Usage(input_tokens=50, output_tokens=20, cost_usd=0.0005),
        ),
    )


def calls(name: str, **args: object) -> tuple[ModelDelta, ...]:
    """A turn that calls one tool."""
    return (
        Completed(
            message=Message(
                role=Role.ASSISTANT,
                content_blocks=(ToolUse(id=f"c_{name}", name=name, arguments=args),),
            ),
            finish=FinishReason.TOOL_USE,
            usage=Usage(input_tokens=50, output_tokens=20, cost_usd=0.0005),
        ),
    )


CONFIG = RouterConfig(
    roles={ModelRole.MAIN: "big", ModelRole.FAST: "small"},
    models={
        "big": ModelSpec(name="big", provider="anthropic", model="claude-x", price_in=3.0),
        "small": ModelSpec(name="small", provider="mlx", model="qwen-local"),
    },
)


def router_with(models: Mapping[str, ModelClient]) -> Router:
    """A router whose factory hands back the named scripted clients."""

    def build(spec: ModelSpec, *, transport: object = None, env: object = None) -> ModelClient:
        del transport, env
        return models[spec.name]

    async def no_sleep(delay: float) -> None:
        del delay

    return Router(CONFIG, build=build, sleep=no_sleep)


def session_for(
    tmp_path: Path,
    fast: ScriptedModel,
    *,
    big: ScriptedModel | None = None,
    ledger: Ledger | None = None,
) -> tuple[SubagentSession, object]:
    """A base registry (no `task`) plus a wired subagent session."""
    ctx = context(tmp_path)
    base = build_registry(ctx)
    router = router_with({"small": fast, "big": big or ScriptedModel([says("main")], "big")})
    runner, subagents = build_subagent_runner(router, base, ledger=ledger, session_id="s1")
    return subagents, runner


# --------------------------------------------------------------------------- #
# Decision 1: the fast model, always
# --------------------------------------------------------------------------- #


async def test_a_subagent_runs_on_the_fast_model_not_the_main_one(tmp_path: Path) -> None:
    """The whole point of the routing rule, now actually enforced end to end."""
    fast = ScriptedModel([says("the fast model answered")], "fast")
    big = ScriptedModel([says("the main model answered")], "big")
    subagents, _ = session_for(tmp_path, fast, big=big)

    answer = await subagents.run("find the parser", EXPLORE)

    assert "fast model answered" in answer
    assert len(fast.requests) == 1
    assert big.requests == [], "the main model must not be touched by a subagent"


EXPLORE = SubagentType(
    name="explore",
    description="Read-only sweep.",
    tools=("read", "glob", "grep", "ls"),
    system_prompt="You are a search subagent. Report only the conclusion.",
)


async def test_the_subagent_system_prompt_reaches_the_model(tmp_path: Path) -> None:
    fast = ScriptedModel([says("done")])
    subagents, _ = session_for(tmp_path, fast)
    await subagents.run("q", EXPLORE)
    assert "search subagent" in fast.requests[0].system


async def test_the_prompt_is_the_only_message_the_child_starts_with(tmp_path: Path) -> None:
    """A subagent starts fresh: no parent history, so the prompt must be complete."""
    fast = ScriptedModel([says("done")])
    subagents, _ = session_for(tmp_path, fast)
    await subagents.run("the whole task", EXPLORE)
    messages = fast.requests[0].messages
    assert len(messages) == 1
    assert messages[0].role is Role.USER
    assert messages[0].text == "the whole task"


# --------------------------------------------------------------------------- #
# Decision 2: no recursion
# --------------------------------------------------------------------------- #


async def test_a_subagent_never_gets_the_task_tool(tmp_path: Path) -> None:
    """Unbounded nesting is one request fanning out with no budget over the tree."""
    fast = ScriptedModel([says("done")])
    ctx = context(tmp_path)
    base = build_registry(ctx)
    router = router_with({"small": fast, "big": ScriptedModel([says("x")])})
    _, subagents = build_subagent_runner(router, base)

    greedy = SubagentType(
        name="greedy",
        description="Wants everything.",
        tools=("read", "task", "grep"),
        system_prompt="x",
    )
    child = subagents.child_registry(greedy)
    names = {spec.name for spec in child.specs()}
    assert "task" not in names
    assert names == {"read", "grep"}


def test_the_forbidden_set_is_exactly_task() -> None:
    assert set(FORBIDDEN_IN_SUBAGENTS) == {"task"}


# --------------------------------------------------------------------------- #
# Decision 3: no escalation
# --------------------------------------------------------------------------- #


async def test_a_subagent_cannot_ask_the_user_for_approval(tmp_path: Path) -> None:
    from ronin.core.types import Budget, DangerLevel, ToolSpec

    policy = SubagentPolicy(Budget())
    decision = await policy.approve(
        ToolSpec(
            name="write",
            description="Write a file.",
            danger_level=DangerLevel.MUTATING,
            requires_approval=True,
        ),
        ToolUse(id="c1", name="write", arguments={}),
        rendered="write foo.py",
    )
    assert not decision.approved
    assert "cannot ask the user" in decision.reason


async def test_a_gated_tool_in_a_child_is_denied_and_reported(tmp_path: Path) -> None:
    """A denial is an observation the child can report, not a hang."""
    write_file(tmp_path, "a.py", "x = 1\n")
    fast = ScriptedModel(
        [
            calls("write", path="a.py", content="clobbered"),
            says("I was not allowed to write."),
        ]
    )
    subagents, _ = session_for(tmp_path, fast)
    dangerous = SubagentType(
        name="editor",
        description="Can edit.",
        tools=("read", "write"),
        system_prompt="x",
    )
    answer = await subagents.run("change it", dangerous)

    assert "not allowed to write" in answer
    assert (tmp_path / "a.py").read_text() == "x = 1\n", "the write must not have happened"


# --------------------------------------------------------------------------- #
# Decision 4: the child has its own read set
# --------------------------------------------------------------------------- #


async def test_a_child_read_does_not_satisfy_the_parents_write_guard(tmp_path: Path) -> None:
    """Sharing the read set would quietly weaken the rule that saves work."""
    write_file(tmp_path, "a.py", "important = True\n")
    fast = ScriptedModel([calls("read", path="a.py"), says("it sets important")])
    ctx = context(tmp_path)
    base = build_registry(ctx)
    router = router_with({"small": fast, "big": ScriptedModel([says("x")])})
    _, subagents = build_subagent_runner(router, base)

    await subagents.run("what is in a.py?", EXPLORE)

    # The child read it. The parent has not.
    assert not ctx.has_been_read(tmp_path / "a.py")
    blocked = await base.execute(use("write", path="a.py", content="gone"))
    assert not blocked.ok
    assert "has not been read" in blocked.error


async def test_the_child_context_shares_the_root_and_vision(tmp_path: Path) -> None:
    fast = ScriptedModel([says("x")])
    ctx = ToolContext(root=tmp_path, vision=True)
    base = build_registry(ctx)
    router = router_with({"small": fast, "big": ScriptedModel([says("x")])})
    _, subagents = build_subagent_runner(router, base)
    child = subagents.child_context()
    assert child.root == ctx.root
    assert child.vision is True
    assert child.read_files == set()
    assert child.read_files is not ctx.read_files


# --------------------------------------------------------------------------- #
# Decision 5: failure is a value
# --------------------------------------------------------------------------- #


async def test_a_subagent_that_answers_nothing_says_so(tmp_path: Path) -> None:
    fast = ScriptedModel([says("")])
    subagents, _ = session_for(tmp_path, fast)
    answer = await subagents.run("q", EXPLORE)
    assert "finished without answering" in answer


async def test_a_child_cut_off_with_no_answer_says_both_things(tmp_path: Path) -> None:
    """It answered nothing *and* it was cut off. The parent needs both facts."""
    write_file(tmp_path, "a.py", "x\n")
    fast = ScriptedModel([calls("read", path="a.py")])  # never stops calling
    subagents, _ = session_for(tmp_path, fast)
    capped = SubagentType(
        name="explore", description="d", tools=("read",), system_prompt="x", max_iterations=2
    )
    answer = await subagents.run("q", capped)
    assert "without answering" in answer
    assert "max_iterations" in answer
    assert subagents.runs[0].stop_reason == "max_iterations"


async def test_a_partial_answer_is_labelled_partial(tmp_path: Path) -> None:
    """A truncated sweep reported as complete is worse than no sweep at all."""
    write_file(tmp_path, "a.py", "x\n")
    # Narrate-and-call on both turns, so the cap bites with text already emitted.
    fast = ScriptedModel(
        [
            says_and_calls("found two so far. ", "read", path="a.py"),
            says_and_calls("and a third.", "read", path="a.py"),
        ]
    )
    subagents, _ = session_for(tmp_path, fast)
    capped = SubagentType(
        name="explore",
        description="d",
        tools=("read",),
        system_prompt="x",
        max_iterations=2,
    )
    answer = await subagents.run("q", capped)
    assert "found two so far" in answer
    assert "[partial:" in answer
    assert "max_iterations" in answer


async def test_a_type_naming_a_missing_tool_reports_a_config_error(tmp_path: Path) -> None:
    fast = ScriptedModel([says("x")])
    subagents, _ = session_for(tmp_path, fast)
    broken = SubagentType(name="shelly", description="d", tools=("read", "bash"), system_prompt="x")
    answer = await subagents.run("q", broken)
    assert "could not start" in answer
    assert "bash" in answer


async def test_an_interrupt_propagates_rather_than_being_swallowed(tmp_path: Path) -> None:
    class Cancelling:
        never = False

        def capabilities(self) -> Capabilities:
            return CAPS

        async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
            del req
            # `if False: yield` rather than a trailing yield: it makes this an async
            # generator without a statement mypy correctly calls unreachable.
            if self.never:  # pragma: no cover - always false
                yield TextDelta("")
            raise asyncio.CancelledError

    ctx = context(tmp_path)
    base = build_registry(ctx)
    router = router_with({"small": Cancelling(), "big": ScriptedModel([says("x")])})
    _, subagents = build_subagent_runner(router, base)
    with pytest.raises(asyncio.CancelledError):
        await subagents.run("q", EXPLORE)


# --------------------------------------------------------------------------- #
# The parent never pays for the child's context
# --------------------------------------------------------------------------- #


async def test_only_the_final_text_escapes_the_child(tmp_path: Path) -> None:
    """Tool results and intermediate turns stay inside; that is the entire point."""
    write_file(tmp_path, "secret.py", "TOKEN = 'do-not-leak-this-into-the-parent'\n")
    fast = ScriptedModel([calls("read", path="secret.py"), says("secret.py defines one constant.")])
    subagents, _ = session_for(tmp_path, fast)
    answer = await subagents.run("what is in secret.py?", EXPLORE)

    assert answer == "secret.py defines one constant."
    assert "do-not-leak-this-into-the-parent" not in answer
    assert "TOKEN" not in answer


async def test_the_run_log_records_what_the_child_cost(tmp_path: Path) -> None:
    fast = ScriptedModel([says("done", tokens=400)])
    subagents, _ = session_for(tmp_path, fast)
    await subagents.run("q", EXPLORE)
    assert len(subagents.runs) == 1
    run = subagents.runs[0]
    assert run.kind == "explore"
    assert run.spent_tokens == 600  # 400 in + 200 out
    assert "explore:" in run.line()
    assert "none of it in the parent's context" in subagents.summary()


async def test_an_empty_run_log_says_so(tmp_path: Path) -> None:
    subagents, _ = session_for(tmp_path, ScriptedModel([says("x")]))
    assert subagents.summary() == "no subagents ran"


async def test_subagent_spend_is_billed_to_the_fast_role(tmp_path: Path) -> None:
    """The ledger's per-role split is where a routing mistake becomes visible."""
    ledger = Ledger(tmp_path / "usage.db", clock=lambda: 1000.0)
    fast = ScriptedModel([says("done", tokens=300)])
    subagents, _ = session_for(tmp_path, fast, ledger=ledger)
    await subagents.run("q", EXPLORE)
    await subagents.run("q2", EXPLORE)

    breakdown = ledger.role_totals("s1")
    assert set(breakdown) == {ModelRole.FAST}
    assert breakdown[ModelRole.FAST].requests == 2
    assert ModelRole.MAIN not in breakdown


# --------------------------------------------------------------------------- #
# The assembled session
# --------------------------------------------------------------------------- #


async def test_build_session_produces_a_registry_containing_task(tmp_path: Path) -> None:
    fast = ScriptedModel([says("child answer")])
    big = ScriptedModel([says("parent answer")])
    ctx = context(tmp_path)
    session = build_session(
        router_with({"small": fast, "big": big}),
        ctx,
        base_tools=build_registry(ctx),
    )
    names = {spec.name for spec in session.registry.specs()}
    assert "task" in names
    assert "read" in names


async def test_the_assembled_task_tool_actually_reaches_the_fast_model(
    tmp_path: Path,
) -> None:
    """End to end: the model calls `task`, and a nested turn runs on `fast`."""
    fast = ScriptedModel([says("the child's conclusion")])
    big = ScriptedModel([says("parent")])
    ctx = context(tmp_path)
    session = build_session(
        router_with({"small": fast, "big": big}),
        ctx,
        base_tools=build_registry(ctx),
    )

    result = await session.registry.execute(
        use("task", prompt="sweep the repo", subagent_type="explore")
    )
    assert result.ok
    assert result.content == "the child's conclusion"
    assert len(fast.requests) == 1
    assert big.requests == []
    assert len(session.subagents.runs) == 1


async def test_the_main_client_is_the_main_model(tmp_path: Path) -> None:
    fast = ScriptedModel([says("child")])
    big = ScriptedModel([says("parent")])
    ctx = context(tmp_path)
    session = build_session(
        router_with({"small": fast, "big": big}),
        ctx,
        base_tools=build_registry(ctx),
    )
    chunks = [chunk async for chunk in session.main.stream(system="s", messages=(), tools=())]
    assert chunks
    assert len(big.requests) == 1
    assert fast.requests == []


def test_a_fresh_state_carries_the_prompt_and_the_root(tmp_path: Path) -> None:
    fast = ScriptedModel([says("x")])
    ctx = context(tmp_path)
    session = build_session(
        router_with({"small": fast, "big": ScriptedModel([says("y")])}),
        ctx,
        base_tools=build_registry(ctx),
    )
    state = session.state("do the thing")
    assert state.messages[0].text == "do the thing"
    assert state.cwd == str(tmp_path)


def test_default_context_resolves_the_root(tmp_path: Path) -> None:
    ctx = default_context(tmp_path)
    assert ctx.root == tmp_path.resolve()
    assert ctx.read_files == set()


async def test_a_parent_turn_is_billed_to_the_main_role(tmp_path: Path) -> None:
    """Found by the demo: the ledger only ever saw subagents, so the per-role split
    — the thing that makes a routing mistake visible — showed one row on a session
    where the main model had plainly just run."""
    from ronin.core.types import Budget as CoreBudget

    ledger = Ledger(tmp_path / "usage.db", clock=lambda: 1000.0)
    fast = ScriptedModel([says("child")])
    big = ScriptedModel([says("parent")])
    ctx = context(tmp_path)
    session = build_session(
        router_with({"small": fast, "big": big}),
        ctx,
        base_tools=build_registry(ctx),
        ledger=ledger,
        session_id="s1",
    )

    await session.subagents.run("q", EXPLORE)
    session.record_turn(
        session.state("x").__class__(budget=CoreBudget(spent_tokens=900, spent_usd=0.03)),
        request_id="turn-1",
    )

    breakdown = ledger.role_totals("s1")
    assert set(breakdown) == {ModelRole.MAIN, ModelRole.FAST}
    assert breakdown[ModelRole.MAIN].input_tokens == 900
    assert breakdown[ModelRole.MAIN].cost_usd == 0.03


async def test_recording_a_turn_without_a_ledger_is_a_no_op(tmp_path: Path) -> None:
    from ronin.core.types import Budget as CoreBudget

    fast = ScriptedModel([says("child")])
    ctx = context(tmp_path)
    session = build_session(
        router_with({"small": fast, "big": ScriptedModel([says("p")])}),
        ctx,
        base_tools=build_registry(ctx),
    )
    session.record_turn(
        session.state("x").__class__(budget=CoreBudget(spent_tokens=1)), request_id="t"
    )


# --------------------------------------------------------------------------- #
# Decision 1, second half: a definition on disk may name a role
# --------------------------------------------------------------------------- #

#: The shape the builtin `fixer` has: it edits and reruns tests, and its definition
#: asks for the main model because that work is not mechanical.
FIXER = SubagentType(
    name="fixer",
    description="Given a failing test, makes it pass.",
    tools=("read", "glob", "grep", "write", "edit", "multi_edit"),
    system_prompt="You are the fixer subagent.",
    model_role="main",
)


async def test_a_definition_may_name_the_main_model_and_it_is_honoured(tmp_path: Path) -> None:
    """A subagent that edits code is not mechanical work; the config may say so.

    Guarded by decision 1's other half: only a definition reaches this field, so the
    model cannot upgrade its own tier by naming one in a `task` call.
    """
    fast = ScriptedModel([says("fast answered")], "fast")
    big = ScriptedModel([says("main answered")], "big")
    subagents, _ = session_for(tmp_path, fast, big=big)

    answer = await subagents.run("make test_x pass", FIXER)

    assert "main answered" in answer
    assert len(big.requests) == 1
    assert fast.requests == [], "an explicit model_role must not fall through to fast"


async def test_an_unknown_role_name_falls_back_to_fast_rather_than_crashing(
    tmp_path: Path,
) -> None:
    """A typo in one markdown file must not take the session down."""
    fast = ScriptedModel([says("fast answered")], "fast")
    big = ScriptedModel([says("main answered")], "big")
    subagents, _ = session_for(tmp_path, fast, big=big)
    typo = SubagentType(
        name="odd",
        description="d",
        tools=("read",),
        system_prompt="p",
        model_role="fastest",
    )

    answer = await subagents.run("q", typo)

    assert "fast answered" in answer
    assert big.requests == []


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("", ModelRole.FAST),
        ("fast", ModelRole.FAST),
        ("main", ModelRole.MAIN),
        ("plan", ModelRole.PLAN),
        ("nonsense", ModelRole.FAST),
    ],
)
def test_role_for_maps_every_declared_value(
    tmp_path: Path, declared: str, expected: ModelRole
) -> None:
    subagents, _ = session_for(tmp_path, ScriptedModel([says("x")]))
    kind = SubagentType(
        name="k", description="d", tools=("read",), system_prompt="p", model_role=declared
    )
    assert subagents.role_for(kind) is expected


async def test_the_child_is_billed_to_the_role_it_actually_ran_on(tmp_path: Path) -> None:
    """Billing every child to `fast` would hide the one thing the split exists for."""
    ledger = Ledger(tmp_path / "usage.db", clock=lambda: 1000.0)
    fast = ScriptedModel([says("fast answered", tokens=10)], "fast")
    big = ScriptedModel([says("main answered", tokens=10)], "big")
    subagents, _ = session_for(tmp_path, fast, big=big, ledger=ledger)

    await subagents.run("make test_x pass", FIXER)

    breakdown = ledger.role_totals("s1")
    assert set(breakdown) == {ModelRole.MAIN}, (
        f"a child that ran on `main` must be billed to `main`, got {set(breakdown)}"
    )


# --------------------------------------------------------------------------- #
# Decision 3, second half: standing permission is not escalation
# --------------------------------------------------------------------------- #


async def test_the_default_policy_still_refuses_every_gated_call(tmp_path: Path) -> None:
    """Unchanged behaviour when nothing is injected: deny, with a reason to report."""
    policy = SubagentPolicy(Budget(max_tokens=1000))
    decision = await policy.approve(
        ToolSpec(
            name="bash",
            description="d",
            danger_level=DangerLevel.DESTRUCTIVE,
            requires_approval=True,
        ),
        use("bash", command="pytest -q"),
        rendered="pytest -q",
    )
    assert decision.approved is False
    assert "cannot ask the user" in decision.reason


async def test_an_injected_policy_lets_a_child_act_on_standing_permission(
    tmp_path: Path,
) -> None:
    """The `fixer` fix: a gated call the caller's policy allows actually runs.

    This is the contradiction the injection exists to resolve — the builtin `fixer`
    declares `bash` and, under the deny-everything default, could never run the test
    it exists to fix. The injected policy still cannot *ask*; it can only act on
    permission that already existed.
    """
    seen: list[str] = []

    class Standing:
        """Approves without asking anyone — what a real PolicyEngine does for an
        allowlisted command, and nothing more."""

        def __init__(self, budget: Budget) -> None:
            self.budget = budget

        async def approve(
            self, spec: ToolSpec, use_: ToolUse, *, rendered: str
        ) -> ApprovalDecision:
            del spec, use_
            seen.append(rendered)
            return ApprovalDecision(approved=True, reason="allowlisted by the user")

        def check_budget(self, budget: Budget) -> str | None:
            return "token_budget" if budget.exhausted else None

        def cancelled(self) -> bool:
            return False

    ctx = context(tmp_path)
    base = build_registry(ctx)
    # A file the model has not read: `write`'s own guard refuses to clobber one it
    # has, and that guard is not what this test is about.
    fast = ScriptedModel(
        [says_and_calls("writing", "write", path="fixed.txt", content="after\n"), says("done")]
    )
    router = router_with({"small": fast, "big": ScriptedModel([says("m")], "big")})
    _, subagents = build_subagent_runner(router, base, policy_for=Standing)

    writer = SubagentType(
        name="writer", description="d", tools=("read", "write"), system_prompt="p"
    )
    await subagents.run("write the file", writer)

    assert seen, "the injected policy was never consulted"
    assert (tmp_path / "fixed.txt").read_bytes() == b"after\n"


async def test_a_subagent_can_be_given_a_repo_map_the_main_client_does_not_carry(
    tmp_path: Path,
) -> None:
    """The map belongs in the system prompt, which children never see.

    A caller that puts the repo map in the *system* text — where it lands in the
    provider's cached prefix — passes `repo_map=""`, and before this parameter existed
    that silently left children with no map at all. An `explore` child that cannot see
    the shape of the repo greps blind, so it is the caller most likely to need one.
    """
    fast = ScriptedModel([says("child")])
    big = ScriptedModel([says("parent")])
    ctx = context(tmp_path)
    session = build_session(
        router_with({"small": fast, "big": big}),
        ctx,
        base_tools=build_registry(ctx),
        repo_map="",
        subagent_repo_map="# repo map\nsrc/parser.py: parse()",
    )

    await session.subagents.run("find the parser", EXPLORE)

    assert "src/parser.py" in fast.requests[0].prefix
    main_request = session.main.build_request(system="you are ronin", messages=(), tools=())
    assert "src/parser.py" not in main_request.prefix, (
        "the main client's map lives in the system text, not its stable prefix"
    )


async def test_the_subagent_map_defaults_to_the_main_one(tmp_path: Path) -> None:
    """Omitting it must not change behaviour for a caller that never thought about it."""
    fast = ScriptedModel([says("child")])
    ctx = context(tmp_path)
    session = build_session(
        router_with({"small": fast, "big": ScriptedModel([says("m")], "big")}),
        ctx,
        base_tools=build_registry(ctx),
        repo_map="# shared map",
    )
    await session.subagents.run("q", EXPLORE)
    assert "# shared map" in fast.requests[0].prefix
