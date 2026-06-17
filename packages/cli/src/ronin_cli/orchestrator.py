"""The Orchestrator — decompose one goal into provider-agnostic subagents.

Ronin already has several multi-model primitives, and this builds *alongside*
them rather than duplicating any:

- **consensus** asks the SAME question of N models and synthesizes one answer.
- **dojo** has N models attempt the SAME change and a judge crowns one diff.
- **swarm** runs a FIXED architect -> implementer -> reviewer pipeline.

The Orchestrator is the missing fourth shape: a **planner** decomposes a single
goal into a graph of DIFFERENT subtasks, each subtask becomes a **subagent** with
its own role, system prompt, tool posture (read-only vs mutating), and — the
differentiator — its own ASSIGNED PROVIDER/MODEL resolved through Ronin's
existing backend selection. Independent subtasks run in parallel; mutating
subtasks run in isolated git worktrees (the same machinery the dojo uses) so
their edits cannot collide; finally a synthesis pass fuses the subagent outputs
into one answer (and, for code subtasks, surfaces each isolated diff for review).

What is reused (not reinvented):

- ``config_for_spec`` + ``build_single_provider`` (runner.py) resolve a
  ``provider[:model]`` spec to a real backend — this is how a subagent gets its
  provider.
- ``run_code_agent`` (code_mode.py) executes each subagent with the full coding
  toolbelt, in read-only or mutating mode.
- ``git_worktree`` / ``worktree_diff`` / ``is_git_repo`` (worktree.py) isolate
  parallel mutating subagents — the dojo's worktree machinery.
- ``parse_model_spec`` (consensus.py) parses the ``provider[:model]`` form.
- The judge/synthesis provider is built with ``build_single_provider`` — the same
  pattern consensus and dojo use.

The planner, the plan model, the level scheduler, and the synthesis prompt are
pure / dependency-injectable, so the whole pipeline is tested offline with
``FakeProvider`` and a monkeypatched ``run_code_agent`` — no network, no keys,
no spend. See ``tests/test_orchestrator.py``.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from ronin_agent_patterns import Message

from .config import RoninConfig

# A subagent that may mutate the repo runs read_only=False; one that only
# investigates runs read_only=True. The planner decides per subtask.
_PLANNER_SYSTEM = """You are the PLANNER of a multi-agent orchestrator. You are given
ONE goal. Decompose it into the SMALLEST set of concrete subtasks that, run by
specialist subagents, accomplish the goal. Each subtask becomes its own subagent.

Rules:
- Prefer few, well-scoped subtasks over many tiny ones.
- Mark a subtask kind="edit" if it must change files, kind="research" if it only
  reads/investigates and returns findings.
- Give each subtask an id (short, unique, snake_case) and list the ids of other
  subtasks it depends_on (only research findings can be depended on; edit
  subtasks run isolated and are merged by the human, so nothing depends on them).
- Independent subtasks (no shared dependency) WILL run in parallel — exploit that.
- Keep the goal's intent; do not invent unrelated work.

Return ONLY a JSON object wrapped in <plan></plan> tags with this shape:
<plan>
{
  "goal": "the restated goal",
  "subtasks": [
    {"id": "explore_auth", "kind": "research", "role": "explore how auth works",
     "prompt": "full instruction for this subagent", "depends_on": []}
  ]
}
</plan>
The "role" is a short label; "prompt" is the complete instruction the subagent
receives. Do not assign providers — the orchestrator does that."""

_SYNTHESIS_SYSTEM = """You are the SYNTHESIZER of a multi-agent orchestrator. You are
given the original goal and each subagent's result. Produce ONE coherent answer
for the user: integrate the research findings, summarize what each edit subagent
changed, call out conflicts or gaps, and state whether the goal is met. Be
concrete and concise. Do not invent results the subagents did not report."""

_PLAN_RE = re.compile(r"<plan>(.*?)</plan>", re.DOTALL)

# Subtask kinds.
KIND_RESEARCH = "research"
KIND_EDIT = "edit"

_RESEARCH_SYSTEM = """You are a research subagent in an orchestrated run. Work
read-only: explore the codebase (read_file / list_files / search_files / glob) and
reason. Do NOT edit or run anything. Return a concise, self-contained result — the
findings the orchestrator asked for, nothing else."""

_EDIT_SYSTEM = """You are an implementation subagent in an orchestrated run, working
inside your OWN ISOLATED git worktree — a private checkout of the repository. Make
the change you were asked for: read, edit, create files, and run commands freely.
You are sandboxed; nothing you do touches the main checkout or any other subagent
until the human reviews your diff. When finished, briefly summarize what you
changed and why. Keep the work tightly scoped to your subtask."""


class SubTask(BaseModel):
    """One unit of the plan, executed by exactly one subagent.

    ``provider`` is the differentiator: a ``provider[:model]`` spec resolved
    through Ronin's backend selection (``config_for_spec``). ``None`` means run on
    the orchestrator's base config. The orchestrator assigns it (from the roster);
    the planner never does.
    """

    id: str
    kind: str = KIND_RESEARCH  # "research" (read-only) | "edit" (mutating)
    role: str = ""             # short human label for the subagent
    prompt: str                # the full instruction handed to the subagent
    depends_on: list[str] = Field(default_factory=list)
    provider: str | None = None  # provider[:model] spec, or None -> base config

    @property
    def mutating(self) -> bool:
        return self.kind == KIND_EDIT


class Plan(BaseModel):
    """A decomposition of one goal into subtasks (a small dependency DAG)."""

    goal: str
    subtasks: list[SubTask] = Field(default_factory=list)

    def levels(self) -> list[list[SubTask]]:
        """Topologically group subtasks into levels that can each run in parallel.

        Level 0 is every subtask with no unmet dependency; level k+1 is every
        subtask whose dependencies are all satisfied by levels <= k. A dependency
        on an unknown id is treated as already-satisfied (the planner referenced a
        subtask it did not emit) so the run never deadlocks. A genuine cycle stops
        scheduling and the remaining subtasks are returned as a final level so the
        run still terminates."""
        by_id = {s.id: s for s in self.subtasks}
        done: set[str] = set()
        levels: list[list[SubTask]] = []
        remaining = list(self.subtasks)
        while remaining:
            ready = [
                s for s in remaining
                if all(dep in done or dep not in by_id for dep in s.depends_on)
            ]
            if not ready:  # cycle / stuck — emit the rest as one final level
                levels.append(remaining)
                break
            levels.append(ready)
            done.update(s.id for s in ready)
            ready_ids = {s.id for s in ready}
            remaining = [s for s in remaining if s.id not in ready_ids]
        return levels


@dataclass
class SubResult:
    """The outcome of running one subagent."""

    id: str
    role: str
    label: str            # provider:model the subagent actually ran on
    kind: str
    ok: bool
    output: str = ""
    diff: str = ""        # only for edit subtasks (their isolated worktree diff)
    error: str | None = None


@dataclass
class OrchestratorResult:
    goal: str
    plan: Plan
    subresults: list[SubResult] = field(default_factory=list)
    synthesis: str = ""

    @property
    def succeeded(self) -> list[SubResult]:
        return [r for r in self.subresults if r.ok]

    @property
    def edits(self) -> list[SubResult]:
        return [r for r in self.subresults if r.kind == KIND_EDIT and r.diff.strip()]


def _provider_label(base: RoninConfig, spec: str | None) -> str:
    """The ``provider:model`` a subtask's spec resolves to (for display/logs)."""
    cfg = _config_for(base, spec)
    return f"{cfg.provider}:{cfg.resolved_model()}"


def _config_for(base: RoninConfig, spec: str | None) -> RoninConfig:
    """Resolve a subtask's ``provider[:model]`` spec to a RoninConfig via Ronin's
    existing backend selection. ``None`` -> the base config unchanged."""
    if not spec:
        return base
    from .consensus import parse_model_spec
    from .runner import config_for_spec

    return config_for_spec(base, parse_model_spec(spec))


def make_plan(
    base: RoninConfig,
    goal: str,
    *,
    planner_provider=None,
    max_tokens: int = 2048,
) -> Plan:
    """Ask the planner model to decompose ``goal`` into a Plan.

    ``planner_provider`` is injectable (a built ``LLMProvider``); when ``None`` it
    is built from the base config with ``build_single_provider`` — the same way
    consensus/dojo build their judge. Raises ``ValueError`` if the model does not
    emit a valid ``<plan>`` block."""
    if planner_provider is None:
        from .runner import build_single_provider

        planner_provider = build_single_provider(base)
    resp = planner_provider.complete(
        system=_PLANNER_SYSTEM,
        messages=[Message(role="user", content=f"GOAL:\n{goal}")],
        tools=[],
        max_tokens=max_tokens,
    )
    match = _PLAN_RE.search(resp.text or "")
    if not match:
        raise ValueError(
            f"planner did not emit a <plan> block: {(resp.text or '')[:200]}"
        )
    try:
        plan = Plan.model_validate_json(match.group(1).strip())
    except ValidationError as exc:
        raise ValueError(f"planner emitted an invalid plan: {exc}") from exc
    if not plan.subtasks:
        raise ValueError("planner produced a plan with no subtasks")
    return plan


def assign_providers(plan: Plan, roster: list[str] | None) -> Plan:
    """Assign a ``provider[:model]`` to each subtask from ``roster`` (round-robin).

    This is where provider-agnostic subagents become real: pass
    ``["anthropic", "gemini", "cerebras:gpt-oss-120b"]`` and successive subtasks
    are pinned to successive backends. An empty/None roster leaves every subtask
    on the base config. Pure — returns a new Plan, does not mutate the input."""
    if not roster:
        return plan
    pinned: list[SubTask] = []
    for i, st in enumerate(plan.subtasks):
        # An explicit per-subtask provider (rare) always wins over the roster.
        spec = st.provider or roster[i % len(roster)]
        pinned.append(st.model_copy(update={"provider": spec}))
    return Plan(goal=plan.goal, subtasks=pinned)


def _run_research(
    base: RoninConfig,
    st: SubTask,
    root: Path | str,
    max_iterations: int,
    run_code_agent: Callable,
) -> SubResult:
    cfg = _config_for(base, st.provider)
    label = f"{cfg.provider}:{cfg.resolved_model()}"
    try:
        res = run_code_agent(
            cfg, st.prompt, root=root, console=None, yolo=True, read_only=True,
            max_iterations=max_iterations, base_system=_RESEARCH_SYSTEM,
            include_image_tool=False,
        )
    except Exception as exc:  # noqa: BLE001 — one subagent failing must not sink the run
        return SubResult(st.id, st.role, label, st.kind, ok=False, error=str(exc))
    if not res.success:
        return SubResult(st.id, st.role, label, st.kind, ok=False,
                         output=res.output or "", error=res.error)
    return SubResult(st.id, st.role, label, st.kind, ok=True,
                     output=res.output or "(no output)")


def _run_edit(
    base: RoninConfig,
    st: SubTask,
    root: Path | str,
    max_iterations: int,
    run_code_agent: Callable,
) -> SubResult:
    """Run a mutating subagent inside its own isolated git worktree (dojo
    machinery), capturing its diff. The main checkout is never touched."""
    from .worktree import NotAGitRepo, git_worktree, worktree_diff

    cfg = _config_for(base, st.provider)
    label = f"{cfg.provider}:{cfg.resolved_model()}"
    try:
        with git_worktree(root, label=f"orch-{st.id}") as wt:
            res = run_code_agent(
                cfg, st.prompt, root=wt, console=None, yolo=True, read_only=False,
                max_iterations=max_iterations, base_system=_EDIT_SYSTEM,
                include_image_tool=False,
            )
            diff = worktree_diff(wt)
    except NotAGitRepo as exc:
        return SubResult(st.id, st.role, label, st.kind, ok=False,
                         error=f"cannot isolate: {exc}")
    except Exception as exc:  # noqa: BLE001
        return SubResult(st.id, st.role, label, st.kind, ok=False, error=str(exc))
    if not res.success:
        return SubResult(st.id, st.role, label, st.kind, ok=False,
                         output=res.output or "", diff=diff, error=res.error)
    return SubResult(st.id, st.role, label, st.kind, ok=True,
                     output=res.output or "(no summary)", diff=diff)


def _dependency_context(st: SubTask, done: dict[str, SubResult]) -> str:
    """Inline the outputs of a subtask's satisfied research dependencies into its
    prompt, so a later subagent sees what the earlier ones found."""
    parts = []
    for dep in st.depends_on:
        r = done.get(dep)
        if r is not None and r.ok and r.output:
            parts.append(f"--- finding from `{dep}` ({r.role}) ---\n{r.output}")
    if not parts:
        return ""
    return "Context from prior subagents:\n\n" + "\n\n".join(parts) + "\n\n---\n\n"


def synthesize(
    base: RoninConfig,
    goal: str,
    subresults: list[SubResult],
    *,
    synth_provider=None,
    max_tokens: int = 2048,
) -> str:
    """Fuse the subagents' results into one answer. ``synth_provider`` is
    injectable; when ``None`` it is built from the base config. With zero or one
    successful result there is nothing to fuse, so the lone output (or a plain
    failure note) is returned without a model call."""
    good = [r for r in subresults if r.ok]
    if not good:
        return "no subagent produced a result — check provider credentials / the goal."
    if len(good) == 1 and not good[0].diff:
        return good[0].output
    if synth_provider is None:
        from .runner import build_single_provider

        synth_provider = build_single_provider(base)
    board = []
    for r in subresults:
        head = f"### subagent `{r.id}` — {r.role} [{r.label}] ({r.kind})"
        if r.ok:
            body = r.output or "(no output)"
            if r.diff.strip():
                body += f"\n\n[isolated diff]\n{r.diff[:4000]}"
        else:
            body = f"FAILED: {r.error or 'unknown error'}"
        board.append(f"{head}\n{body}")
    prompt = f"GOAL:\n{goal}\n\nSUBAGENT RESULTS:\n\n" + "\n\n".join(board)
    resp = synth_provider.complete(
        system=_SYNTHESIS_SYSTEM,
        messages=[Message(role="user", content=prompt)],
        tools=[],
        max_tokens=max_tokens,
    )
    return resp.text or "(synthesizer returned nothing)"


def run_orchestrator(
    base: RoninConfig,
    goal: str,
    *,
    roster: list[str] | None = None,
    root: Path | str = ".",
    plan: Plan | None = None,
    planner_provider=None,
    synth_provider=None,
    max_iterations: int = 20,
    max_workers: int = 4,
    on_event: Callable[[str, dict], None] | None = None,
) -> OrchestratorResult:
    """Decompose ``goal``, run the subagents level-by-level (parallel within a
    level), then synthesize.

    - ``roster`` — ``provider[:model]`` specs assigned round-robin to subtasks
      (the provider-agnostic differentiator). None -> every subagent on the base.
    - ``plan`` — supply a Plan to skip the planner (used by tests / re-runs).
    - ``planner_provider`` / ``synth_provider`` — injectable providers for
      offline testing. None -> built from the base config.
    - ``on_event`` — optional ``(name, data)`` hook for live progress
      (``plan``, ``level``, ``subtask_done``, ``synthesis``).

    Edit subtasks run isolated in git worktrees; if ``root`` is not a git repo
    they fail gracefully with a clear message rather than crashing the run.
    """
    def emit(name: str, data: dict) -> None:
        if on_event is not None:
            on_event(name, data)

    if plan is None:
        plan = make_plan(base, goal, planner_provider=planner_provider)
    plan = assign_providers(plan, roster)
    emit("plan", {"goal": plan.goal, "subtasks": [s.model_dump() for s in plan.subtasks]})

    from .code_mode import run_code_agent

    done: dict[str, SubResult] = {}
    ordered: list[SubResult] = []
    for level_idx, level in enumerate(plan.levels()):
        emit("level", {"index": level_idx, "ids": [s.id for s in level]})

        def _run(st: SubTask) -> SubResult:
            # Fold in any dependency findings, then dispatch by kind.
            ctx = _dependency_context(st, done)
            task = st.model_copy(update={"prompt": ctx + st.prompt}) if ctx else st
            if st.mutating:
                return _run_edit(base, task, root, max_iterations, run_code_agent)
            return _run_research(base, task, root, max_iterations, run_code_agent)

        if len(level) == 1:
            results = [_run(level[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(level))) as ex:
                results = list(ex.map(_run, level))  # ex.map preserves level order
        for r in results:
            done[r.id] = r
            ordered.append(r)
            emit("subtask_done", {"id": r.id, "ok": r.ok, "label": r.label,
                                  "kind": r.kind})

    synthesis = synthesize(base, plan.goal, ordered, synth_provider=synth_provider)
    emit("synthesis", {"text": synthesis})
    return OrchestratorResult(goal=goal, plan=plan, subresults=ordered,
                              synthesis=synthesis)


# --------------------------------------------------------------------------
# Delegate tool — let the MAIN coding agent call the orchestrator mid-run.
# --------------------------------------------------------------------------

def build_orchestrate_tool(
    config: RoninConfig,
    root: Path | str,
    *,
    roster: list[str] | None = None,
    max_iterations: int = 20,
):
    """An ``orchestrate`` tool: hand the orchestrator a goal and get back a
    synthesized result. This is the delegate mechanism — the main agent decides a
    job is big and multi-part, calls ``orchestrate``, and the orchestrator plans,
    fans out provider-agnostic subagents (research read-only, edits isolated in
    worktrees), and returns one synthesized answer plus any diffs.

    Complements ``task`` / ``parallel_task`` / ``isolated_task`` (which the agent
    drives one call at a time): ``orchestrate`` does the decomposition for it."""
    from ronin_agent_patterns import Tool

    def _orchestrate(goal: str) -> str:
        result = run_orchestrator(
            config, goal, roster=roster, root=root,
            max_iterations=max_iterations,
        )
        lines = [f"Orchestrated {len(result.subresults)} subagent(s):"]
        for r in result.subresults:
            status = "ok" if r.ok else f"FAILED ({r.error})"
            lines.append(f"- `{r.id}` [{r.label}] {r.kind}: {status}")
        lines.append("")
        lines.append("Synthesis:")
        lines.append(result.synthesis)
        for r in result.edits:
            lines.append(f"\n--- diff from `{r.id}` (isolated worktree) ---\n{r.diff}")
        return "\n".join(lines)

    return Tool(
        name="orchestrate",
        description=(
            "Decompose a big, multi-part goal into a plan of subtasks and run each "
            "as its own subagent (research subagents explore read-only; edit "
            "subagents make changes isolated in their own git worktree, in "
            "parallel where independent), then synthesize one result. Use for goals "
            "that split into several distinct pieces of work. Args: goal (the full "
            "objective)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The full objective to decompose and run."},
            },
            "required": ["goal"],
        },
        handler=_orchestrate,
    )


def parse_roster(spec: str | None) -> list[str]:
    """Parse a comma-separated ``provider[:model]`` roster string into a list.

    e.g. ``"anthropic,gemini,cerebras:gpt-oss-120b"`` ->
    ``["anthropic", "gemini", "cerebras:gpt-oss-120b"]``. Blank entries dropped.
    Pure. Note: a model id may itself contain a colon, which is fine — the spec is
    resolved later by ``parse_model_spec`` (first colon splits provider/model)."""
    if not spec:
        return []
    return [s.strip() for s in spec.split(",") if s.strip()]


def render_result(console, result: OrchestratorResult) -> None:
    """Print the plan, each subagent's status, and the synthesis."""
    from rich.panel import Panel

    from .theme import ACCENT, ERR, MUTE, OK

    console.print(f"[bold {ACCENT}]plan[/bold {ACCENT}] [dim]{result.plan.goal}[/dim]")
    for st in result.plan.subtasks:
        dep = f" [dim]<- {', '.join(st.depends_on)}[/dim]" if st.depends_on else ""
        console.print(f"  [cyan]{st.id}[/cyan] [dim]({st.kind})[/dim] "
                      f"{st.role} [{MUTE}]{_provider_label_safe(result, st)}[/{MUTE}]{dep}")
    console.print()
    for r in result.subresults:
        title = f"{'●' if r.ok else '✗'} {r.id} — {r.role} [{r.label}]"
        if r.ok:
            body = r.output or "(no output)"
            if r.diff.strip():
                body += f"\n\n[dim]({len(r.diff.splitlines())} diff lines in isolated worktree)[/dim]"
            console.print(Panel(body, title=f"[{OK}]{title}[/{OK}]",
                                border_style=MUTE, padding=(0, 1)))
        else:
            console.print(Panel(f"[{ERR}]{r.error or 'failed'}[/{ERR}]",
                                title=f"[{ERR}]{title}[/{ERR}]",
                                border_style=ERR, padding=(0, 1)))
    console.print(Panel(result.synthesis,
                        title=f"[bold {ACCENT}]synthesis[/bold {ACCENT}]",
                        border_style=ACCENT, padding=(0, 1)))


def _provider_label_safe(result: OrchestratorResult, st: SubTask) -> str:
    """The label a subtask ran on (from its result if present, else its spec)."""
    for r in result.subresults:
        if r.id == st.id:
            return r.label
    return st.provider or "base"
