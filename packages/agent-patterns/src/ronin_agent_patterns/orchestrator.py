"""Multi-agent orchestrator: decompose a goal, assign provider-agnostic
sub-agents, run them (parallel where independent), synthesize the results.

How this differs from the other multi-agent primitives in this package:

- ``SupervisorAgent`` lets one orchestrator model *dynamically* delegate to
  named sub-agents via ``delegate_to_<name>`` tools. The decomposition is
  implicit: the orchestrator decides, turn by turn, whom to call. Great for
  open-ended routing.
- ``PlannerExecutorAgent`` makes an explicit plan, then runs every step on ONE
  executor with ONE tool set and ONE provider, sequentially.

``OrchestratorAgent`` sits between them. It makes an explicit plan like the
planner/executor, but each subtask is routed to a *named sub-agent* that can run
on its *own provider/model and own tool subset* like the supervisor, and
independent subtasks run *in parallel*. The differentiator is honest and
concrete: sub-agents are provider-agnostic. The planner can put the
architecture subtask on Claude, the bulk implementation on a fast free model,
and the review on a third vendor, all in one run, because each
``OrchestratorSubAgent`` carries its own ``LLMProvider`` (built however the host
likes — see the CLI's ``config_for_spec``/``build_single_provider``).

The whole thing is provider-agnostic and fully offline-testable: hand every
sub-agent (and the planner) a ``FakeProvider`` and the orchestration runs with
no network and no API keys. See ``tests/test_orchestrator.py``.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .providers import AnthropicProvider, LLMProvider, Message
from .durable import BudgetExceeded, RunBudget, RunJournal
from .react import OnStep, ReActAgent
from .types import AgentResult, Step, Tool


class OrchestratorSubAgent(BaseModel):
    """A specialist worker the orchestrator can assign subtasks to.

    Role + system prompt + a tool subset + an assigned provider/model. When run,
    it drives a real ``ReActAgent`` sub-loop on its own provider, so two
    sub-agents in the same orchestration can sit on two different vendors'
    models. ``provider=None`` means "use the orchestrator's default provider".
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: str
    description: str
    system: str
    tools: list[Tool] = Field(default_factory=list)
    provider: LLMProvider | None = None
    max_iterations: int = 8
    max_total_tokens: int | None = Field(default=None, gt=0)
    max_wall_time_seconds: float | None = Field(default=None, gt=0)

    def label(self, default_provider: LLMProvider | None = None) -> str:
        prov = self.provider or default_provider
        model = getattr(prov, "model", "?") if prov is not None else "?"
        return f"{self.role}@{model}"


class Subtask(BaseModel):
    """One unit of work in an orchestration plan, assigned to a sub-agent.

    ``depends_on`` lists the ids of subtasks that must finish first; subtasks
    with no outstanding dependencies run in parallel. ``assignee`` is the
    ``role`` of the ``OrchestratorSubAgent`` that should do it.
    """

    id: str
    description: str
    assignee: str
    depends_on: list[str] = Field(default_factory=list)


class OrchestrationPlan(BaseModel):
    """A decomposition of a goal into assigned, dependency-ordered subtasks."""

    goal: str
    subtasks: list[Subtask] = Field(default_factory=list)


class SubtaskResult(BaseModel):
    """The outcome of running one subtask on its assigned sub-agent."""

    subtask_id: str
    assignee: str
    success: bool
    output: str = ""
    error: str | None = None
    iterations: int = 0
    provider: str = ""
    usage: dict[str, int | float] = Field(default_factory=dict)


class OrchestrationResult(BaseModel):
    """Everything the orchestrator produced: the plan, every subtask result, and
    the synthesized final answer."""

    success: bool
    goal: str
    plan: OrchestrationPlan | None = None
    subtask_results: list[SubtaskResult] = Field(default_factory=list)
    output: str = ""
    error: str | None = None
    trace: list[Step] = Field(default_factory=list)
    usage: dict[str, int | float] = Field(default_factory=dict)
    run_id: str | None = None


# Hook fired when a sub-agent is about to run a subtask. (subtask, subagent_label)
OnSubtaskStart = Callable[[Subtask, str], None]


_PLANNER_SYSTEM = (
    "You are the PLANNER of a multi-agent system. You decompose a goal into a "
    "small number of concrete subtasks and assign each one to the most suitable "
    "specialist sub-agent. Keep the plan minimal: prefer the fewest subtasks that "
    "still cover the goal. Mark dependencies honestly — only add a dependency when "
    "a subtask truly needs another's output, so independent work can run in "
    "parallel."
)


class OrchestratorAgent(BaseModel):
    """Plan -> assign -> run (parallel where independent) -> synthesize.

    Pick this when:
    - The goal genuinely splits into specialist subtasks (research, implement,
      review, ...) rather than one execution thread.
    - You want each specialist on a *different* provider/model (the
      provider-agnostic differentiator).
    - Independent subtasks should run concurrently.

    The orchestrator owns three roles, each of which can be a different provider:
    the *planner* (decomposes), the *sub-agents* (do the work, one provider
    each), and the *synthesizer* (writes the final answer, defaults to the
    planner's provider).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    goal_system: str = ""
    sub_agents: list[OrchestratorSubAgent] = Field(default_factory=list)
    # The planner/synthesizer brain. Sub-agents fall back to this when they have
    # no provider of their own.
    provider: LLMProvider | None = None
    # Optional dedicated synthesizer provider; defaults to ``provider``.
    synthesizer_provider: LLMProvider | None = None
    max_parallel: int = 4
    max_planner_tokens: int = 2048
    max_synth_tokens: int = 1024
    # Optional host-owned plan contract.  These are role-level requirements so
    # the core stays independent of any one CLI's workflow vocabulary.
    required_roles: set[str] = Field(default_factory=set)
    role_dependencies: dict[str, set[str]] = Field(default_factory=dict)
    acceptance_roles: set[str] = Field(default_factory=set)
    # A reservation ceiling, not an estimate: the plan's sum of per-subtask
    # iteration caps must fit before any provider call begins.
    max_total_subtask_iterations: int | None = Field(default=None, gt=0)

    # Backward-compat shortcuts (used only if provider is not supplied):
    model: str | None = None
    api_key: str | None = None

    def model_post_init(self, _ctx: object) -> None:
        if self.provider is None:
            self.provider = AnthropicProvider(
                model=self.model or "claude-sonnet-4-6",
                api_key=self.api_key,
            )

    # ---- planning -------------------------------------------------------

    def _roster_block(self) -> str:
        lines = [f"- {sa.role}: {sa.description}" for sa in self.sub_agents]
        return "\n".join(lines) if lines else "(no specialists registered)"

    def _make_plan(self, goal: str, *, budget: RunBudget | None = None) -> OrchestrationPlan:
        assert self.provider is not None
        if budget is not None:
            decision = budget.before_provider()
            if not decision.allowed:
                raise BudgetExceeded(decision.reason)
        roles = [sa.role for sa in self.sub_agents]
        prompt = (
            f"GOAL:\n{goal}\n\n"
            f"Available specialist sub-agents (assign each subtask to one of "
            f"these roles):\n{self._roster_block()}\n\n"
            "Return a JSON object with two fields:\n"
            "  'goal' (string)\n"
            "  'subtasks' (list of objects, each with: 'id' (short unique string), "
            "'description' (what to do), 'assignee' (one of the roles above), and "
            "'depends_on' (list of subtask ids that must finish first, possibly "
            "empty)).\n"
            "Wrap the JSON in <plan></plan> tags. Use only the listed roles."
        )
        system = (
            f"{self.goal_system}\n\n{_PLANNER_SYSTEM}" if self.goal_system else _PLANNER_SYSTEM
        )
        response = self.provider.complete(
            system=system,
            messages=[Message(role="user", content=prompt)],
            tools=[],
            max_tokens=self.max_planner_tokens,
        )
        if budget is not None:
            budget.record_provider_usage(response.usage)
        match = re.search(r"<plan>(.*?)</plan>", response.text, re.DOTALL)
        raw = match.group(1).strip() if match else response.text.strip()
        try:
            plan = OrchestrationPlan.model_validate_json(raw)
        except ValidationError as exc:
            raise ValueError(f"Planner emitted invalid plan JSON: {exc}") from exc
        self._validate_plan(plan)
        return plan

    def _validate_plan(self, plan: OrchestrationPlan) -> None:
        if not plan.subtasks:
            raise ValueError("plan has no subtasks")
        roles = {sa.role for sa in self.sub_agents}
        ids = [st.id for st in plan.subtasks]
        if len(set(ids)) != len(ids):
            raise ValueError(f"plan has duplicate subtask ids: {ids}")
        id_set = set(ids)
        for st in plan.subtasks:
            if roles and st.assignee not in roles:
                raise ValueError(
                    f"subtask '{st.id}' assigned to unknown role '{st.assignee}' "
                    f"(known: {sorted(roles)})"
                )
            for dep in st.depends_on:
                if dep not in id_set:
                    raise ValueError(
                        f"subtask '{st.id}' depends on unknown id '{dep}'"
                    )
                if dep == st.id:
                    raise ValueError(f"subtask '{st.id}' depends on itself")

        roles_in_plan = {st.assignee for st in plan.subtasks}
        missing_roles = sorted(self.required_roles - roles_in_plan)
        if missing_roles:
            raise ValueError(
                "plan is missing required workflow roles: " + ", ".join(missing_roles)
            )
        by_id = {st.id: st for st in plan.subtasks}
        for role, upstream_roles in self.role_dependencies.items():
            for st in plan.subtasks:
                if st.assignee != role:
                    continue
                direct_roles = {by_id[dep].assignee for dep in st.depends_on}
                missing_upstream = sorted(set(upstream_roles) - direct_roles)
                if missing_upstream:
                    raise ValueError(
                        f"subtask '{st.id}' assigned to '{role}' must directly depend on "
                        f"a subtask from: {', '.join(missing_upstream)}"
                    )
        # An acceptance role must be a different registered specialist from the
        # role it accepts.  The host supplies dependency constraints that make
        # the accepted work explicit; this prevents self-approval by role.
        for role in self.acceptance_roles:
            if role not in roles_in_plan:
                raise ValueError(f"plan is missing required acceptance role: {role}")
            if role in self.role_dependencies.get(role, set()):
                raise ValueError(f"acceptance role '{role}' cannot depend on itself")

        if self.max_total_subtask_iterations is not None:
            by_role = {sa.role: sa for sa in self.sub_agents}
            reserved = sum(by_role[st.assignee].max_iterations for st in plan.subtasks)
            if reserved > self.max_total_subtask_iterations:
                raise ValueError(
                    "plan reserves "
                    f"{reserved} subtask iterations, above governance limit "
                    f"{self.max_total_subtask_iterations}"
                )

    # ---- scheduling -----------------------------------------------------

    @staticmethod
    def schedule(plan: OrchestrationPlan) -> list[list[Subtask]]:
        """Group subtasks into dependency *waves*: each wave is a set of subtasks
        whose dependencies are all satisfied by earlier waves, so the whole wave
        can run in parallel. Pure; raises ValueError on a dependency cycle."""
        by_id = {st.id: st for st in plan.subtasks}
        done: set[str] = set()
        waves: list[list[Subtask]] = []
        remaining = list(plan.subtasks)
        while remaining:
            ready = [st for st in remaining if all(d in done for d in st.depends_on)]
            if not ready:
                stuck = [st.id for st in remaining]
                raise ValueError(f"dependency cycle or missing dep among: {stuck}")
            waves.append(ready)
            done.update(st.id for st in ready)
            ready_ids = {st.id for st in ready}
            remaining = [st for st in remaining if st.id not in ready_ids]
        return waves

    # ---- execution ------------------------------------------------------

    def _run_subtask(
        self,
        subtask: Subtask,
        sub: OrchestratorSubAgent,
        context: str,
        on_subtask_start: OnSubtaskStart | None,
        budget: RunBudget | None = None,
    ) -> SubtaskResult:
        provider = sub.provider or self.provider
        provider_model = str(getattr(provider, "model", "?"))
        if on_subtask_start is not None:
            try:
                on_subtask_start(subtask, sub.label(self.provider))
            except Exception:  # noqa: BLE001 — a narration hook must not break the run
                pass
        agent = ReActAgent(
            system=sub.system,
            tools=sub.tools,
            provider=provider,
            max_iterations=sub.max_iterations,
            max_total_tokens=sub.max_total_tokens,
            max_wall_time_seconds=sub.max_wall_time_seconds,
        )
        prompt = subtask.description
        if context:
            prompt = (
                f"{subtask.description}\n\n"
                f"Context from completed upstream subtasks:\n{context}"
            )
        try:
            res: AgentResult = agent.run(prompt, budget=budget)
        except Exception as exc:  # noqa: BLE001 — one sub-agent failing != whole run dies
            return SubtaskResult(
                subtask_id=subtask.id, assignee=subtask.assignee,
                success=False, error=f"{type(exc).__name__}: {exc}",
                provider=provider_model,
            )
        return SubtaskResult(
            subtask_id=subtask.id,
            assignee=subtask.assignee,
            success=res.success,
            output=res.output,
            error=res.error,
            iterations=res.iterations,
            provider=provider_model,
            usage=res.usage,
        )

    def _synthesize(
        self,
        goal: str,
        results: list[SubtaskResult],
        *,
        budget: RunBudget | None = None,
    ) -> tuple[str, dict[str, int | float]]:
        provider = self.synthesizer_provider or self.provider
        assert provider is not None
        if budget is not None:
            decision = budget.before_provider()
            if not decision.allowed:
                raise BudgetExceeded(decision.reason)
        board = "\n\n".join(
            f"### subtask {r.subtask_id} (by {r.assignee}) — "
            f"{'ok' if r.success else 'FAILED'}\n"
            f"{r.output or r.error or '(no output)'}"
            for r in results
        )
        prompt = (
            f"GOAL:\n{goal}\n\n"
            f"Your sub-agents completed these subtasks:\n\n{board}\n\n"
            "Synthesize their work into a single coherent final answer for the "
            "goal. Be concise and concrete."
        )
        system = (
            f"{self.goal_system}\n\nYou are the SYNTHESIZER: combine the sub-agents' "
            "results into one answer." if self.goal_system
            else "You are the SYNTHESIZER: combine the sub-agents' results into one answer."
        )
        resp = provider.complete(
            system=system,
            messages=[Message(role="user", content=prompt)],
            tools=[],
            max_tokens=self.max_synth_tokens,
        )
        if budget is not None:
            budget.record_provider_usage(resp.usage)
        return resp.text.strip(), resp.usage

    def run(
        self,
        goal: str,
        *,
        on_step: OnStep | None = None,
        on_subtask_start: OnSubtaskStart | None = None,
        journal: RunJournal | None = None,
        journal_run_id: str | None = None,
        budget: RunBudget | None = None,
        resume_state: dict[str, object] | None = None,
    ) -> OrchestrationResult:
        """Decompose ``goal``, assign and run sub-agents (parallel where the plan
        allows), then synthesize a final answer.

        ``on_step`` is fired for every plan/result/error Step as it happens (live
        narration). ``on_subtask_start`` is fired with (subtask, sub-agent label)
        right before each subtask runs — handy for showing which provider is
        about to work which piece. ``journal`` checkpoints the plan and each
        completed wave before a later provider action; ``resume_state`` comes
        from :meth:`resume` and never re-runs a completed subtask.
        """
        trace: list[Step] = []
        usage: dict[str, int | float] = {"input_tokens": 0, "output_tokens": 0}
        plan: OrchestrationPlan | None = None
        results_by_id: dict[str, SubtaskResult] = {}
        active_run_id = ""
        final_output = ""

        def emit(step: Step) -> None:
            trace.append(step)
            if on_step is not None:
                on_step(step)

        def state(phase: str) -> dict[str, object]:
            return {
                "schema_version": 1,
                "runtime": "orchestrator",
                "goal": goal,
                "phase": phase,
                "plan": plan.model_dump(mode="json") if plan is not None else None,
                "subtask_results": [
                    result.model_dump(mode="json") for result in results_by_id.values()
                ],
                "trace": [step.model_dump(mode="json") for step in trace],
                "usage": usage,
                "budget": budget.snapshot() if budget is not None else {},
                "budget_limits": budget.limits.as_dict() if budget is not None else {},
                "final_output": final_output,
            }

        def checkpoint(phase: str) -> None:
            if journal is not None:
                journal.checkpoint(active_run_id, state(phase))

        def complete(result: OrchestrationResult, status: str) -> OrchestrationResult:
            if journal is not None:
                journal.finish(active_run_id, status=status, error=result.error or "")
            return result

        if resume_state is not None:
            if journal is None or not journal_run_id:
                raise ValueError("journal and journal_run_id are required when resuming an orchestration")
            if resume_state.get("runtime") != "orchestrator":
                raise ValueError("durable state does not belong to an orchestration")
            saved_goal = resume_state.get("goal")
            if not isinstance(saved_goal, str) or not saved_goal.strip():
                raise ValueError("durable orchestration state is missing its goal")
            if goal and goal != saved_goal:
                raise ValueError("resume goal does not match the durable orchestration state")
            goal = saved_goal
            active_run_id = journal_run_id
            raw_plan = resume_state.get("plan")
            if raw_plan is not None:
                plan = OrchestrationPlan.model_validate(raw_plan)
            raw_results = resume_state.get("subtask_results", [])
            if not isinstance(raw_results, list):
                raise ValueError("durable orchestration state has invalid subtask results")
            results_by_id = {
                result.subtask_id: result
                for result in (SubtaskResult.model_validate(item) for item in raw_results)
            }
            raw_trace = resume_state.get("trace", [])
            if isinstance(raw_trace, list):
                trace.extend(Step.model_validate(item) for item in raw_trace)
            raw_usage = resume_state.get("usage", {})
            if isinstance(raw_usage, dict):
                usage = {
                    str(key): value for key, value in raw_usage.items()
                    if isinstance(value, (int, float))
                }
            if budget is not None:
                saved_budget = resume_state.get("budget", {})
                if isinstance(saved_budget, dict):
                    budget.restore(saved_budget)
            journal.append_event(active_run_id, "orchestration_resumed", {
                "completed_subtasks": len(results_by_id),
            })
        elif journal is not None:
            active_run_id = journal.start(state("planning"), run_id=journal_run_id)
            journal.append_event(active_run_id, "orchestration_started", {})

        # 1. PLAN
        if plan is None:
            try:
                plan = self._make_plan(goal, budget=budget)
            except BudgetExceeded as exc:
                emit(Step(kind="error", content=f"planning blocked: {exc}"))
                return complete(OrchestrationResult(
                    success=False, goal=goal, error=str(exc), trace=trace, usage=usage,
                    run_id=active_run_id or None,
                ), "blocked")
            except Exception as exc:  # noqa: BLE001
                emit(Step(kind="error", content=f"planning failed: {exc}"))
                return complete(OrchestrationResult(
                    success=False, goal=goal, error=str(exc), trace=trace, usage=usage,
                    run_id=active_run_id or None,
                ), "failed")
            emit(Step(kind="plan", content=plan.model_dump()))
            checkpoint("executing")
            if journal is not None:
                journal.append_event(active_run_id, "orchestration_planned", {
                    "subtask_count": len(plan.subtasks),
                })

        try:
            waves = self.schedule(plan)
        except ValueError as exc:
            emit(Step(kind="error", content=f"scheduling failed: {exc}"))
            return complete(OrchestrationResult(
                success=False, goal=goal, plan=plan, error=str(exc),
                trace=trace, usage=usage, run_id=active_run_id or None,
            ), "failed")

        by_role = {sa.role: sa for sa in self.sub_agents}

        # 2. RUN each wave; subtasks within a wave run in parallel.
        for wave_number, wave in enumerate(waves, start=1):
            pending_wave = [subtask for subtask in wave if subtask.id not in results_by_id]
            if not pending_wave:
                continue

            def run_in_wave(st: Subtask) -> SubtaskResult:
                sub = by_role.get(st.assignee)
                if sub is None:
                    return SubtaskResult(
                        subtask_id=st.id, assignee=st.assignee, success=False,
                        error=f"no sub-agent registered for role '{st.assignee}'",
                    )
                # Feed completed upstream outputs as context.
                ctx_parts = [
                    f"[{dep}] {results_by_id[dep].output}"
                    for dep in st.depends_on
                    if dep in results_by_id and results_by_id[dep].output
                ]
                return self._run_subtask(
                    st, sub, "\n\n".join(ctx_parts), on_subtask_start, budget,
                )

            if len(pending_wave) == 1:
                wave_results = [run_in_wave(pending_wave[0])]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(self.max_parallel, len(pending_wave))
                ) as ex:
                    wave_results = list(ex.map(run_in_wave, pending_wave))

            for st, res in zip(pending_wave, wave_results):
                results_by_id[res.subtask_id] = res
                emit(Step(
                    kind="tool_result",
                    content={
                        "subtask": res.subtask_id,
                        "assignee": res.assignee,
                        "success": res.success,
                        "output": res.output,
                        "error": res.error,
                        "provider": res.provider,
                    },
                    metadata={"iterations": res.iterations},
                ))
            checkpoint("executing")
            if journal is not None:
                journal.append_event(active_run_id, "orchestration_wave_completed", {
                    "wave": wave_number,
                    "subtasks": [result.subtask_id for result in wave_results],
                    "successful": sum(1 for result in wave_results if result.success),
                })

        ordered = [results_by_id[st.id] for st in plan.subtasks]
        all_ok = all(r.success for r in ordered)

        # 3. SYNTHESIZE
        checkpoint("synthesizing")
        if journal is not None:
            journal.append_event(active_run_id, "orchestration_synthesis_started", {})
        synthesis_blocked = False
        try:
            final, synth_usage = self._synthesize(goal, ordered, budget=budget)
            usage["input_tokens"] += synth_usage.get("input_tokens", 0)
            usage["output_tokens"] += synth_usage.get("output_tokens", 0)
        except BudgetExceeded as exc:
            synthesis_blocked = True
            emit(Step(kind="error", content=f"synthesis blocked: {exc}"))
            final = self._fallback_summary(goal, ordered)
            if journal is not None:
                journal.append_event(active_run_id, "orchestration_synthesis_blocked", {})
        except Exception as exc:  # noqa: BLE001 — fall back to a deterministic summary
            emit(Step(kind="error", content=f"synthesis failed: {exc}"))
            final = self._fallback_summary(goal, ordered)
            if journal is not None:
                journal.append_event(active_run_id, "orchestration_synthesis_failed", {
                    "error_type": type(exc).__name__,
                })
        final_output = final
        emit(Step(kind="final", content=final))
        if budget is not None:
            usage = budget.snapshot()
        checkpoint("completed")
        return complete(OrchestrationResult(
            success=all_ok and not synthesis_blocked,
            goal=goal,
            plan=plan,
            subtask_results=ordered,
            output=final,
            error=("synthesis budget reached" if synthesis_blocked
                   else None if all_ok else "one or more subtasks failed"),
            trace=trace,
            usage=usage,
            run_id=active_run_id or None,
        ), "blocked" if synthesis_blocked else "completed" if all_ok else "failed")

    def resume(
        self,
        run_id: str,
        journal: RunJournal,
        *,
        on_step: OnStep | None = None,
        on_subtask_start: OnSubtaskStart | None = None,
        budget: RunBudget | None = None,
    ) -> OrchestrationResult:
        """Resume an interrupted orchestration without replaying completed waves."""
        return self.run(
            "",
            on_step=on_step,
            on_subtask_start=on_subtask_start,
            journal=journal,
            journal_run_id=run_id,
            budget=budget,
            resume_state=journal.resume(run_id),
        )

    @staticmethod
    def _fallback_summary(goal: str, results: list[SubtaskResult]) -> str:
        lines = [f"Goal: {goal}", "", "Subtask results:"]
        for r in results:
            status = "ok" if r.success else f"FAILED ({r.error})"
            lines.append(f"- {r.subtask_id} [{r.assignee}] {status}: {r.output[:200]}")
        return "\n".join(lines)
