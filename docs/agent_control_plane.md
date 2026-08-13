# Agent Control Plane

Ronin can expose a large specialist catalog without starting a large number of
agents. The control plane converts a goal into a bounded, governed team and
keeps a durable record of the work it did.

## Routing

`ronin util agents "TASK" --root PROJECT` combines task tags with local repository
signals. The signals are derived offline from the existing BM25 repository map:
the files relevant to the task, their symbols, file extensions, and root project
markers such as `pyproject.toml`, `package.json`, and `Dockerfile`.

Every selected profile has an explanation: core roles show `core workflow role`;
specialists show the matching `task:` and `repo:` tags. The planner receives
only the selected team. The default is eight profiles, and the hard ceiling is
32, so a catalog with thousands of profiles cannot create unbounded prompt,
cost, or concurrency pressure.

## Workflow Contracts

`ronin util orchestrate --write` applies the `engineering-change-v1` contract:

1. `researcher` establishes repository evidence.
2. `implementer` directly depends on research.
3. `reviewer` and `tester` directly depend on implementation.
4. Review and test are independent acceptance roles; an implementation role
   cannot approve its own result.

The core validates these role-level requirements against the planner's JSON
plan before a sub-agent starts. The final state also runs Ronin's existing
typed pipeline artifact checker. Prose that cannot be parsed as a structured
handoff stays `unknown`; it is never upgraded to a successful acceptance.

Read-only orchestration remains flexible for exploration. The `investigation-v1`
contract is available to callers that need research followed by an independent
review, without forcing that cost on every CLI exploration.

## Governance

The CLI has conservative defaults and explicit controls:

```bash
ronin util orchestrate "add timeout handling" --write \
  --max-agents 8 --max-parallel 4 \
  --max-subtask-iterations 12 \
  --max-subtask-tokens 50000 \
  --max-total-subtask-iterations 96 \
  --agent-timeout 300
```

- `--max-parallel` bounds an independent execution wave.
- `--max-subtask-iterations` is the per-agent ReAct turn ceiling.
- `--max-subtask-tokens` optionally stops an agent before its next provider
  request once reported input plus output tokens reach the ceiling.
- `--max-total-subtask-iterations` is a preflight reservation ceiling. A plan
  that exceeds it is rejected before any provider call.
- `--agent-timeout` is propagated to each agent's existing wall-clock budget.

Provider transport retries remain provider-owned. Ronin records observed
subtask/provider health as `healthy` or `degraded` based on actual outcomes; it
does not invent a health check or claim a provider is available without evidence.

`ronin util agent-route` consumes explicit quality/cost/latency model evidence and
these local observations to produce a role-to-model roster recommendation. It
uses higher quality weighting for writers and higher efficiency weighting for
exploration. The command does not perform implicit failover or mutate provider
configuration; an operator may pass its printed roster to orchestration.

Objective benchmark rows from `ronin util bench` are saved as local model
scorecards. SWE-bench or judge reports may be imported with `ronin util
scorecards import`; `agent-route --use-scorecards` then substitutes a matching
stored quality score while preserving the caller's explicit cost and latency
assumptions. Provider health is observational: a temporary cooldown follows a
real failure and a later real success clears it.

## Shared Task State

While an orchestration runs, Ronin writes an atomic JSON record at:

```text
.ronin/agent-runs/agent-<timestamp>-<id>.json
```

It includes the routing evidence, workflow, governance limits, live plan,
subtask status, event log, provider-health observations, handoff report, and
final output. A stopped run therefore remains inspectable rather than looking
as if it silently completed. The regular user-level run archive continues to
power `ronin ui` and records the task-state id for correlation.

Implementation roles receive separate detached Git worktrees. Review and test
roles inspect the implementation candidate tree, and the final state records
which roles produced an isolated diff. The parent checkout remains untouched.

Failed or interrupted records can be resumed with `ronin util agent-recover
RUN_ID`. Recovery creates a new task record linked to the original and hands
the planner only bounded status data for the predecessor's planned subtasks.
It never marks old output as validated or automatically reuses an unreviewed
patch.

## Kernel Reliability Boundaries

The control plane uses the same kernel reliability contract as the terminal
agent rather than estimating its own context or inventing separate recovery
state:

- **Provider-aware context accounting.** Anthropic and first-party OpenAI
  requests use their native request-counting APIs. Ollama, compatible, local,
  and custom endpoints without a registered tokenizer use Ronin's documented
  UTF-8 estimate. That result is labelled `estimated`; it is not a provider
  guarantee, billing record, or hard context limit. See
  [context token counting](architecture/context_token_counting.md).
- **Reasoning normalization.** Provider adapters declare either
  `anthropic-thinking`, `openai-effort`, or `none`. Unsupported providers emit
  no reasoning parameter. Gemini is explicitly `none` for normalized effort
  while preserving the opaque tool/thought state required by its provider
  protocol.
- **Durable orchestration.** With `ronin util orchestrate --durable`, the
  kernel journals the accepted plan and each completed dependency wave. A
  single `RunBudget` is shared by concurrent specialists, and recovery resumes
  only unfinished waves. This mode remains read-only; writing work continues
  through governed mission candidates.

The complete execution-surface inventory, including intentionally deferred
one-shot and transport-only paths, is maintained in
[durable surfaces](architecture/durable_surfaces.md). A surface must not claim
crash recovery merely because it calls an agent: it needs a `RunJournal`, a
`RunBudget`, and a defined resume boundary.

Use `ronin util agent-runs` for a terminal view of task state and provider
observations. It reports only real subtask outcomes; no provider is marked
available or healthy without observed successful work.

### Migration and Rollback

This is schema version 1 and introduces no config migration or database. Older
run records remain readable. Removing `.ronin/agent-runs/` only removes local
task-board history; it does not touch source files, checkpoints, or the normal
run archive. To disable this persistence for an embedding, call
`run_orchestrate(..., persist_state=False)`.

## Evaluation

Run the deterministic control-plane regression suite with:

```bash
ronin eval agents
```

The suite uses fixture repository signals and no provider call. It checks that
specialist routing, workflow selection, and governance bounds stay stable in
CI. It complements `ronin eval`, which measures live agent outcomes.

`ronin eval platform` adds offline coverage for the durable queue, local
semantic retrieval fallback, task telemetry, fail-closed sandbox policy,
repository constitution enforcement, hash-chain ledger verification, and
local durable project memory. It also checks linked recovery handoffs,
provider cooldown recovery, scorecard-guided routing, and JSON/TOML preflight
rejection.

### Migration and Rollback for Platform Primitives

The queue, ledger, SQLite memory, and constitution are project-local under
`.ronin/`. Existing installations require no migration. Remove the respective
local file to discard its history or memory; remove `constitution.json` to
return to the permissive policy default. Removing a ledger or memory store does
not change repository files, Git history, provider configuration, or archived
run records.
