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
    usage: dict[str, int] = Field(default_factory=dict)


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

    def _make_plan(self, goal: str) -> OrchestrationPlan:
        assert self.provider is not None
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
    ) -> SubtaskResult:
        provider = sub.provider or self.provider
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
        )
        prompt = subtask.description
        if context:
            prompt = (
                f"{subtask.description}\n\n"
                f"Context from completed upstream subtasks:\n{context}"
            )
        try:
            res: AgentResult = agent.run(prompt)
        except Exception as exc:  # noqa: BLE001 — one sub-agent failing != whole run dies
            return SubtaskResult(
                subtask_id=subtask.id, assignee=subtask.assignee,
                success=False, error=f"{type(exc).__name__}: {exc}",
            )
        return SubtaskResult(
            subtask_id=subtask.id,
            assignee=subtask.assignee,
            success=res.success,
            output=res.output,
            error=res.error,
            iterations=res.iterations,
        )

    def _synthesize(self, goal: str, results: list[SubtaskResult]) -> tuple[str, dict[str, int]]:
        provider = self.synthesizer_provider or self.provider
        assert provider is not None
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
        return resp.text.strip(), resp.usage

    def run(
        self,
        goal: str,
        *,
        on_step: OnStep | None = None,
        on_subtask_start: OnSubtaskStart | None = None,
    ) -> OrchestrationResult:
        """Decompose ``goal``, assign and run sub-agents (parallel where the plan
        allows), then synthesize a final answer.

        ``on_step`` is fired for every plan/result/error Step as it happens (live
        narration). ``on_subtask_start`` is fired with (subtask, sub-agent label)
        right before each subtask runs — handy for showing which provider is
        about to work which piece.
        """
        trace: list[Step] = []
        usage = {"input_tokens": 0, "output_tokens": 0}

        def emit(step: Step) -> None:
            trace.append(step)
            if on_step is not None:
                on_step(step)

        # 1. PLAN
        try:
            plan = self._make_plan(goal)
        except Exception as exc:  # noqa: BLE001
            emit(Step(kind="error", content=f"planning failed: {exc}"))
            return OrchestrationResult(
                success=False, goal=goal, error=str(exc), trace=trace, usage=usage,
            )
        emit(Step(kind="plan", content=plan.model_dump()))

        try:
            waves = self.schedule(plan)
        except ValueError as exc:
            emit(Step(kind="error", content=f"scheduling failed: {exc}"))
            return OrchestrationResult(
                success=False, goal=goal, plan=plan, error=str(exc),
                trace=trace, usage=usage,
            )

        by_role = {sa.role: sa for sa in self.sub_agents}
        results_by_id: dict[str, SubtaskResult] = {}

        # 2. RUN each wave; subtasks within a wave run in parallel.
        for wave in waves:
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
                    st, sub, "\n\n".join(ctx_parts), on_subtask_start,
                )

            if len(wave) == 1:
                wave_results = [run_in_wave(wave[0])]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(self.max_parallel, len(wave))
                ) as ex:
                    wave_results = list(ex.map(run_in_wave, wave))

            for st, res in zip(wave, wave_results):
                results_by_id[res.subtask_id] = res
                emit(Step(
                    kind="tool_result",
                    content={
                        "subtask": res.subtask_id,
                        "assignee": res.assignee,
                        "success": res.success,
                        "output": res.output,
                        "error": res.error,
                    },
                    metadata={"iterations": res.iterations},
                ))

        ordered = [results_by_id[st.id] for st in plan.subtasks]
        all_ok = all(r.success for r in ordered)

        # 3. SYNTHESIZE
        try:
            final, synth_usage = self._synthesize(goal, ordered)
            usage["input_tokens"] += synth_usage.get("input_tokens", 0)
            usage["output_tokens"] += synth_usage.get("output_tokens", 0)
        except Exception as exc:  # noqa: BLE001 — fall back to a deterministic summary
            emit(Step(kind="error", content=f"synthesis failed: {exc}"))
            final = self._fallback_summary(goal, ordered)
        emit(Step(kind="final", content=final))

        return OrchestrationResult(
            success=all_ok,
            goal=goal,
            plan=plan,
            subtask_results=ordered,
            output=final,
            error=None if all_ok else "one or more subtasks failed",
            trace=trace,
            usage=usage,
        )

    @staticmethod
    def _fallback_summary(goal: str, results: list[SubtaskResult]) -> str:
        lines = [f"Goal: {goal}", "", "Subtask results:"]
        for r in results:
            status = "ok" if r.success else f"FAILED ({r.error})"
            lines.append(f"- {r.subtask_id} [{r.assignee}] {status}: {r.output[:200]}")
        return "\n".join(lines)
