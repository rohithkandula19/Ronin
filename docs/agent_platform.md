# Agent Platform Operations

Ronin's agent platform is local-first and bounded. A large profile catalog helps
select expertise; it does not start thousands of processes. Every active team is
limited by the orchestration governance settings, approval policy, and provider
budgets.

## Isolated Write Work

`ronin util orchestrate --write` creates a detached Git worktree for every
implementation role. The parent checkout is never edited by those agents.
Review and test roles inspect the implementer's isolated candidate tree, then
Ronin prints an attributed diff for each changed implementation worktree.

Those diffs are proposals, not automatic merges. Review and apply them through
the normal approval and Git workflow. A repository without Git falls back to a
read-only orchestration rather than writing uncontrolled files.

## Queue and Worker

Use the project-local queue when a person, CI job, or cron task should hand an
agent job to a later worker:

```bash
ronin util agent-queue add "add retry coverage" --write
ronin util agent-queue list
ronin util agent-queue run-next
```

Queue records live in `.ronin/agent-queue.json`. Enqueueing never starts an
agent. `run-next` claims exactly one job, then runs it with the same workflow,
budget, sandbox, and approval boundaries as an interactive orchestration. A
completed, failed, or cancelled job is terminal; re-running a task requires an
explicit new queue entry.

## Local Semantic Retrieval

Coding runs expose `semantic_search` alongside regular repository tools. Ronin
uses configured Ollama or OpenAI-compatible embeddings when intentionally
configured. Otherwise, and always in offline mode, it uses a deterministic
local hashing vector. That fallback never sends project content to an embedding
service and lets code retrieval remain useful on an air-gapped machine.

It is a ranking aid, not proof that a file implements the requested behavior.
Agents still need to read the selected files and run appropriate verification.

## Run Dashboard and Provider Observations

Each orchestration records project-local state in `.ronin/agent-runs/`. Inspect
recent work and observed provider outcomes with:

```bash
ronin util agent-runs
ronin eval platform
```

Provider rows are derived from completed subtasks only. `healthy` means every
observed attempt in the available history succeeded; mixed or failed evidence
is `degraded`. A missing row means Ronin has no evidence, not that a provider is
unavailable.

## Sandbox Policy

```bash
RONIN_BACKEND=seatbelt:no-network ronin util sandbox
RONIN_BACKEND=docker:ronin-sandbox ronin util sandbox
```

The command reports the requested backend without executing a shell command.
When a non-local backend is requested but unavailable, command tools fail
closed: they refuse to run on the host. Docker works anywhere Docker is
available; macOS Seatbelt additionally confines writes to the workspace. Host
execution remains explicit when no sandbox backend is requested.

On Windows, local shell commands use `cmd.exe`; Docker remains the recommended
containment option for repositories whose build or test scripts expect a POSIX
environment. Ronin's objective `ronin bench --models ollama:<model>` surface can
compare local models, while `ronin dev perf` measures repeatable local commands.

## Evaluation

`ronin eval platform` is a provider-free regression suite for the queue,
project telemetry, local semantic fallback, and sandbox fail-closed policy.
Existing `ronin eval agents` continues to cover profile routing, workflow
contracts, and governance limits. For model coding quality, use the existing
SWE-bench-style and agent evaluation surfaces with real providers.
