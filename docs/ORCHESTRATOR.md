# The Orchestrator

A provider-agnostic multi-agent orchestrator for Ronin. It takes one goal,
decomposes it into a small graph of subtasks, runs each subtask as its own
subagent (each on a provider/model you choose), runs independent subtasks in
parallel, isolates mutating subagents in their own git worktrees, and synthesizes
the results into one answer.

This is not a claim that multi-agent orchestration is new. Plenty of frameworks
do planner/subagent decomposition. What this orchestrator does is fit Ronin's
existing provider-agnostic backend so that EACH subagent can run on a DIFFERENT
provider/model, and reuse Ronin's existing worktree isolation so parallel
code-editing subagents do not collide.

## Where it fits among Ronin's existing multi-model primitives

Ronin already ships three multi-model primitives. The orchestrator complements
them; it does not replace or duplicate any.

| Primitive    | Shape                                                      |
|--------------|------------------------------------------------------------|
| consensus    | Same QUESTION asked of N models, one judge synthesizes.    |
| dojo         | Same CHANGE attempted by N models, one judge picks a diff. |
| swarm        | Fixed architect -> implementer -> reviewer pipeline.       |
| orchestrator | DIFFERENT subtasks, each its own subagent + provider, synthesized. |

The orchestrator is the "one goal, decomposed into a heterogeneous plan" shape.
consensus and dojo are "one task, N models race"; swarm is a fixed three-role
pipeline. The orchestrator plans the roles dynamically and assigns a provider per
subtask.

## Module layout

Everything lives in `packages/cli/src/ronin_cli/orchestrator.py` plus the CLI
wiring and a delegate tool. No new package; it sits in the CLI package next to
`consensus.py`, `dojo.py`, and `swarm.py`.

```
packages/cli/src/ronin_cli/orchestrator.py   # the orchestrator
packages/cli/tests/test_orchestrator.py       # offline tests
docs/ORCHESTRATOR.md                           # this file
```

Wiring:

- `main.py` registers the `ronin orchestrate` CLI command.
- `code_mode.py` adds the `orchestrate` delegate tool to the interactive
  agent's toolbelt, alongside `task` / `parallel_task` / `isolated_task`.
- `slash_commands.py` lists `orchestrate` under `/agents`.
- `README.md` documents the command and the delegate tool.

## Data model

```
SubTask
  id          short unique snake_case id
  kind        "research" (read-only) | "edit" (mutating, worktree-isolated)
  role        short human label for the subagent
  prompt      the full instruction handed to the subagent
  depends_on  ids of subtasks whose findings must arrive first
  provider    a "provider[:model]" spec, or None -> the base config
  .mutating   property: True iff kind == "edit"

Plan
  goal        the restated goal
  subtasks    list[SubTask]
  .levels()   topological grouping into parallel-safe levels

SubResult
  id, role, label (provider:model it ran on), kind, ok, output, diff, error

OrchestratorResult
  goal, plan, subresults, synthesis
  .succeeded  the SubResults that ran ok
  .edits      edit SubResults that produced a non-empty diff
```

`SubTask` and `Plan` are pydantic models so the planner's JSON validates on the
way in. `SubResult` and `OrchestratorResult` are dataclasses (internal, not
parsed from a model).

## Public API

```python
from ronin_cli.orchestrator import (
    Plan, SubTask, OrchestratorResult, SubResult,
    make_plan, assign_providers, run_orchestrator,
    synthesize, parse_roster, build_orchestrate_tool, render_result,
)
```

- `make_plan(base, goal, *, planner_provider=None, max_tokens=2048) -> Plan`
  Ask the planner model to decompose the goal. `planner_provider` is injectable;
  when None it is built from the base config with `build_single_provider`. Raises
  `ValueError` if the model does not emit a valid `<plan>` JSON block, or emits a
  plan with no subtasks.

- `assign_providers(plan, roster) -> Plan`
  Assign a `provider[:model]` spec to each subtask round-robin from `roster`.
  This is where provider-agnostic subagents become real. Pure: returns a new
  Plan, does not mutate the input. An explicit per-subtask `provider` always wins
  over the roster. An empty/None roster leaves every subtask on the base config.

- `run_orchestrator(base, goal, *, roster=None, root=".", plan=None,
  planner_provider=None, synth_provider=None, max_iterations=20, max_workers=4,
  on_event=None) -> OrchestratorResult`
  The end-to-end pipeline: plan (or accept a supplied `plan`), assign providers,
  run the subagents level by level (parallel within a level), then synthesize.
  `on_event(name, data)` fires for `plan`, `level`, `subtask_done`, `synthesis`.

- `synthesize(base, goal, subresults, *, synth_provider=None, max_tokens=2048) -> str`
  Fuse the subagents' results into one answer. Injectable provider. With zero or
  one successful non-diff result there is nothing to fuse, so it returns the lone
  output (or a failure note) without a model call.

- `parse_roster(spec) -> list[str]`
  Parse `"anthropic,gemini,cerebras:gpt-oss-120b"` into a list. Pure.

- `build_orchestrate_tool(config, root, *, roster=None, max_iterations=20) -> Tool`
  The delegate tool the main coding agent can call mid-run. The agent decides a
  job is big and multi-part, calls `orchestrate(goal=...)`, and gets back the
  synthesized result plus any isolated diffs.

- `render_result(console, result)`
  Print the plan, each subagent's status, and the synthesis (rich).

## How a subagent gets its provider

This is the core of the design and it reuses Ronin's existing backend selection
end to end. Nothing about provider resolution is new code; the orchestrator only
threads a per-subtask spec through it.

1. The roster is a list of `provider[:model]` specs, e.g.
   `["anthropic", "gemini", "cerebras:gpt-oss-120b"]`.
2. `assign_providers` pins each subtask to a spec round-robin (an explicit
   per-subtask `provider` overrides the roster).
3. At run time, each subtask's spec is resolved by `_config_for`, which calls
   `consensus.parse_model_spec` (the `provider[:model]` parser) and then
   `runner.config_for_spec` (the existing function the failover, consensus, dojo,
   bench, and swarm paths all use to derive a single-provider `RoninConfig`).
4. `run_code_agent` is handed that derived config. Inside it,
   `runner.build_provider` constructs the concrete `LLMProvider`
   (`AnthropicProvider` for `anthropic`, `OpenAICompatProvider` for everything
   else, pointed at the right `base_url`).

So a subagent assigned `gemini` runs on Gemini with its own key from the
per-provider key store, while a sibling subagent assigned `anthropic` runs on
Claude, in the same orchestrated run. The provider-key resolution
(`config.key_for`), the offline forcing (`build_provider` honors `config.offline`
and switches a cloud provider to a local brain), and the failover chain are all
inherited unchanged.

## Execution model

```
goal
  |
  v
make_plan (planner model emits <plan> JSON)
  |
  v
assign_providers (roster -> per-subtask provider:model)
  |
  v
plan.levels()  ->  level 0       level 1            level 2
                   [research]    [research, edit]   [edit]
                      |              |   |              |
                  (parallel within a level via ThreadPoolExecutor)
                      |              |   |              |
   research subagent  +--------------+   |              |
     run_code_agent(read_only=True)      |              |
                                         |              |
   edit subagent      ------------------ + ------------ +
     git_worktree(root) -> run_code_agent(read_only=False) -> worktree_diff
  |
  v
synthesize (synthesizer model fuses all SubResults)
  |
  v
OrchestratorResult (subresults + synthesis + per-edit diffs)
```

- Levels come from `Plan.levels()`, a pure topological grouping. Level 0 is every
  subtask with no unmet dependency; each later level is every subtask whose
  dependencies are satisfied by earlier levels. A dependency on an unknown id is
  treated as satisfied (the planner referenced a subtask it did not emit) so the
  run never deadlocks; a genuine cycle is emitted as one final level so the run
  still terminates.
- Within a level, independent subtasks run concurrently on a thread pool, capped
  at `max_workers`. A single-subtask level runs inline (no pool).
- Research subagents run `read_only=True` in the main checkout (no mutation, safe
  to share).
- Edit subagents run `read_only=False` inside their own `git_worktree`, so
  parallel edits cannot collide; their diff is captured with `worktree_diff`. The
  main checkout is never touched; the user applies diffs after review
  (`ronin orchestrate ... --apply`, gated per diff).
- A research subtask's satisfied dependencies have their outputs folded into the
  dependent subtask's prompt (`_dependency_context`), so a later subagent sees
  what earlier ones found.
- One subagent failing does not sink the run: the failure is captured as a
  `SubResult` with `ok=False` and surfaced in the synthesis board.

## Reused Ronin pieces

| Reused                                         | From            | For                                    |
|------------------------------------------------|-----------------|----------------------------------------|
| `config_for_spec`, `build_single_provider`, `build_provider` | `runner.py` | resolve a subagent's provider/model    |
| `run_code_agent`                               | `code_mode.py`  | execute each subagent (read-only/edit) |
| `git_worktree`, `worktree_diff`, `NotAGitRepo` | `worktree.py`   | isolate parallel mutating subagents    |
| `parse_model_spec`                             | `consensus.py`  | parse the `provider[:model]` form      |
| `_apply_diff`                                  | `kaizen.py`     | apply a winning edit diff (CLI --apply)|
| `Message`, `Tool`, `FakeProvider`             | agent-patterns  | provider calls, the delegate tool, tests |
| `ThreadPoolExecutor` fan-out pattern           | consensus/dojo  | parallel level execution               |

## Offline test strategy

Every test runs with no API keys and no network, matching Ronin's existing
offline test convention (see `test_consensus.py` and `test_parallel_agents.py`).

- The planner and synthesizer take an injectable provider. Tests pass a
  `FakeProvider` with a canned `<plan>...</plan>` response or a canned synthesis
  string. No model is contacted.
- The subagent runner `run_code_agent` is monkeypatched in `code_mode` with a
  fake that returns a `CodeRunResult`. Tests assert on the orchestration
  (decomposition, provider assignment, level scheduling, parallel fan-out,
  dependency context, failure capture, synthesis) rather than any model's output.
- The worktree-isolation tests build a real throwaway git repo in a tmp dir and
  let the fake subagent write a file inside the worktree, then assert the diff was
  captured, the main checkout was untouched, and the worktree was cleaned up.
  This exercises real git plumbing with no LLM.
- Concurrency is proven with a `threading.Barrier` sized to the level: if the
  subagents in a level do not actually run in parallel, the barrier times out and
  the test fails.

Run them:

```
uv run pytest packages/cli/tests/test_orchestrator.py -q
```

Because the offline path forces a local brain (`build_provider` honors
`config.offline`), a real `ronin --offline orchestrate "<goal>"` also runs with
zero network egress: the planner, every subagent, and the synthesizer all run on
the local model.

## CLI

```
ronin orchestrate "<goal>" [--roster a,b,c] [--root .] [--apply]
```

- `--roster` assigns providers round-robin to subtasks. Omit it to run every
  subagent on your current provider.
- `--apply` prompts per edit subagent to apply its isolated diff to your tree
  (gated, defaults to no).

The interactive coding agent (`ronin code` / `ronin`) also has the `orchestrate`
delegate tool: it can decompose a big goal on its own mid-conversation.

## Limits and honesty

- The planner is a model; a bad plan yields a bad run. The decomposition is only
  as good as the planner model and the goal.
- Parallel subagents cost real tokens: N subagents in a level is N model runs.
  Concurrency is capped (`max_workers`, default 4) but spend scales with the plan.
- Edit subagents need a git repo (worktrees are a git feature). Without one, an
  edit subtask fails gracefully with a clear message instead of crashing the run.
- Synthesis is a model summarizing model outputs; verify edit diffs before
  applying them. Nothing is applied without your approval.
