# Agent Control Plane

Ronin can expose a large specialist catalog without starting a large number of
agents. The control plane converts a goal into a bounded, governed team and
keeps a durable record of the work it did.

## Routing

`ronin agents "TASK" --root PROJECT` combines task tags with local repository
signals. The signals are derived offline from the existing BM25 repository map:
the files relevant to the task, their symbols, file extensions, and root project
markers such as `pyproject.toml`, `package.json`, and `Dockerfile`.

Every selected profile has an explanation: core roles show `core workflow role`;
specialists show the matching `task:` and `repo:` tags. The planner receives
only the selected team. The default is eight profiles, and the hard ceiling is
32, so a catalog with thousands of profiles cannot create unbounded prompt,
cost, or concurrency pressure.

## Workflow Contracts

`ronin orchestrate --write` applies the `engineering-change-v1` contract:

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
ronin orchestrate "add timeout handling" --write \
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
