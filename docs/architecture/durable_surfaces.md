# Durable Execution Surfaces

`RunJournal` records atomic checkpoints before provider and tool actions.
`RunBudget` stops new actions before the shared token, cost, time, tool, and
concurrency ceilings are crossed. Neither changes the approval policy of a
surface.

## Complete inventory

The list below is the full inventory of production and shipped-example paths
that invoke `ReActAgent`, `run_code_agent`, `run_agent`, or `run_ask` as of
this document. `Full` means the surface supplies a journal, checkpoint boundary,
and budget. `Partial` means it has a governed per-turn/per-stage kernel run but
does not yet expose a durable whole-session/whole-workflow resume boundary.
`None` is intentionally not described as recoverable.

| Surface | Current coverage | Journal/checkpoint and budget boundary | Decision |
| --- | --- | --- | --- |
| Core `ReActAgent` | Full | Native checkpoint/resume; native budget | Core contract |
| Core `OrchestratorAgent` | Full when supplied runtime | Plan and completed dependency waves; shared parallel budget | Used by durable read-only orchestration |
| `PlannerExecutorAgent` | None | No journal or budget arguments | Deferred: library API needs a generic task-state contract, not the repository-only plan cache |
| `SupervisorAgent` | None | Delegated subagents do not share a durable parent run | Deferred: nested durable handoff semantics require an explicit child-run identity model |
| `ReflexionAgent` | None | Attempt/critique loop has no resume state | Deferred: persisted critique state is needed before retry can safely resume |
| Interactive `ronin code` | Partial | Per-turn journal and bounded budget | Deferred: session history remains owned by session storage, not a resumable kernel transcript |
| One-shot `ronin code` | None | No runtime factory | Deferred: bounded single-request utility path; no resume UX exists |
| ACP editor session | Partial | Per-turn journal/budget; ACP persists its own history | Deferred: ACP protocol has no Ronin kernel-resume operation |
| `ronin util agent` | Full | Per-run journal and budget | Governed command path |
| `ronin util orchestrate --durable` | Full | Plan/wave journal and shared budget | Read-only by design |
| `ronin util orchestrate` without `--durable` | None | No runtime requested | Deferred: preserves existing non-durable CLI behavior |
| Mission implementation turn | Partial | Candidate-local journal; mission budget maps to `RunBudget` | Deferred: mission lifecycle persistence is distinct from a resumable agent transcript |
| `ronin util pipeline` | Partial | Per-stage journal/budget plus existing pipeline state | Deferred: no single cross-stage kernel checkpoint format |
| `ronin util telegram` and briefing | Partial | Per-message/per-briefing journal and budget | Deferred: long polling does not define a conversation-resume protocol |
| `ronin util relay` | None | Transport-only; target owns execution | Intentional: relay must not execute an agent |
| `ronin util schedule run-due` | Partial | Default runner creates a per-task journal/budget | Deferred: custom task runners must opt into kernel runtime explicitly |
| Persistent `ronin_tasks` scheduler | None | Durable task state only | Deferred: an agent-backed runner must pass runtime explicitly |
| `run_ask` API-service and MCP-server callers | None | No runtime passed through | Deferred: request identity, persistence, and resume API are not defined |
| Background agent jobs | None | No runtime passed through | Deferred: background jobs auto-deny approvals and need a distinct recovery policy |
| `ronin swarm` and `ronin scout` | None | Multiple code-agent turns, no shared run | Deferred: role-pipeline recovery must preserve role handoff evidence |
| `ronin dojo` and worktree trials | None | Candidate worktree isolation only | Deferred: trial selection needs durable candidate/result records |
| Code-mode delegated subagents and competing trials | None | No nested journal child runs | Deferred with `SupervisorAgent` child-run model |
| Single-purpose CLI wrappers: research, act, review, fix, kaizen, onboarding, explain, browser, lint repair, refactor, scaffold, estimate, search, slash delegation | None | One-shot code-agent calls | Deferred: no public resume contract for these convenience commands |
| SWE-bench and agent-eval runners | None | Isolated benchmark calls | Intentional: evaluation artifacts, not resumable user sessions |
| Demo app and deployment templates | None | Direct example `ReActAgent` calls | Intentional: example code owns its runtime choices |

## Current governed surfaces

The factory is `ronin_cli.durable_runtime.surface_runtime`. It hashes caller
identity before using it in a run identifier and uses a new random suffix for
each invocation, so recurring tasks cannot collide and raw chat/session ids do
not enter filenames. The current governed surfaces are ReAct, durable
orchestration, interactive coding turns, ACP turns, mission implementation,
pipeline stages, Telegram turns/briefings, `ronin util agent`, and default due
task execution.

Every row marked `Partial` or `None` above is an explicit deferral, not implied
coverage. The deferral is necessary either because the surface has no user
resume contract, because it needs a cross-run/child-run state model, or because
it is deliberately transport/example/evaluation-only. No surface is allowed to
claim crash recovery until its row changes to `Full`.
