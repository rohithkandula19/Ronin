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

Ronin retains every non-empty role patch from a write run under
`.ronin/agent-proposals/<run-id>/`, together with its SHA-256 digest and the
exact `HEAD` revision from which the isolated worktree started. The archive is
not an automatic merge. Inspect and explicitly stage a verified proposal with:

```bash
ronin util proposals list
ronin util proposals show agent-20260730-120000-abc123 --role implementer
ronin util proposals apply agent-20260730-120000-abc123 --yes
```

`apply` refuses a failed source run, a modified patch archive, a moved `HEAD`,
or any dirty index/worktree. It only stages the patch for ordinary Git review;
it never creates a commit, pushes a branch, resolves conflicts, or performs a
three-way merge.

## Fleet Planning

For projects that match a broad span of expertise, turn the generated catalog
into a local, multi-wave schedule:

```bash
ronin util fleet plan "harden the payments API" --write --max-profiles 96 --max-parallel 8
ronin util fleet list
ronin util fleet show fleet-20260731-120000-abc123
```

Fleet planning can rank up to 512 task-relevant profiles, but it schedules no
more than 32 profiles in one wave. Write plans are separated into research,
implementation, and acceptance phases; read-only plans omit implementation.
Plans persist under `.ronin/fleet-plans/` with the selected profile keys and
local routing evidence, but no prompts, providers, agents, shell commands,
queue workers, edits, or merges are started by these commands. A fleet plan is
an inspectable scheduling boundary, not autonomous execution.

## Queue and Worker

Use the project-local queue when a person, CI job, or cron task should hand an
agent job to a later worker:

```bash
ronin util agent-queue add "add retry coverage" --write
ronin util agent-queue list
ronin util agent-queue run-next
ronin util agent-queue run --max-jobs 8 --max-parallel 2
```

Queue records live in `.ronin/agent-queue.json`. Enqueueing never starts an
agent. `run-next` claims exactly one job, then runs it with the same workflow,
budget, sandbox, and approval boundaries as an interactive orchestration. A
completed, failed, or cancelled job is terminal; re-running a task requires an
explicit new queue entry.

`run` claims a bounded batch before it starts workers and records an independent
terminal state for every job. It is deliberately a foreground command: a
scheduler, CI job, or human decides when to invoke it. It does not create a
hidden daemon or bypass provider authentication, sandboxing, workflow checks,
or tool approvals.

## Recovery and Checkpoints

Ronin already creates reversible workspace checkpoints for coding and pipeline
work. Orchestrations additionally persist a live task record, so an interrupted
or failed team can be resumed with an honest handoff:

```bash
ronin util agent-runs
ronin util agent-recover agent-20260730-120000-abc123 --mode original
```

Recovery starts a **new linked run**. It preserves the original selected roles,
roster, and read/write mode, then gives the replacement planner only the prior
subtask statuses: completed, failed, and unfinished. It never treats prior
output as proof or silently skips verification; the new team replans from the
current repository state. Completed runs are not recoverable.

## Repository Constitution

Projects can own a small policy file at `.ronin/constitution.json`:

```bash
ronin util constitution init
ronin util constitution show
```

The schema can protect relative glob paths, cap the active team, require a
specialist role for tagged work, and require a requested sandbox for write
orchestration. Protected files remain readable but `write_file`, `edit_file`,
and `multi_edit` refuse to change them, including inside detached worktrees.
An invalid policy blocks the interactive coding surface and orchestration before
provider work begins. No migration is required: a missing constitution is
permissive and existing projects keep their current behavior.

## Verifiable Autonomy Ledger

Every completed orchestration appends a compact event to
`.ronin/autonomy-ledger.jsonl`. Events are locally hash-chained and contain
goal, error, and diff digests rather than raw prompts, output, or credentials.

```bash
ronin util ledger verify
ronin util ledger show
```

Verification detects a changed event or a broken chain. The ledger is an audit
record, not a permission grant: it never applies a diff or turns a failed run
into an accepted one.

## Shared Project Memory

`RONIN.md`, `CLAUDE.md`, and `AGENTS.md` remain the compatible, prompt-loaded
instruction files. Durable facts and decisions are additionally stored in
`.ronin/project-memory.sqlite` and retrieved with deterministic local hashing:

```bash
ronin util project-memory add "Use pytest -q before merging" --tags test
ronin util project-memory search "how should we verify changes"
```

Coding agents can recall this memory; writing a new fact is approval-gated and
likely API keys or private keys are refused. This store has no cloud dependency
and can be removed independently of source code and instruction files.

## Provider Intelligence and Evaluation Evidence

Every completed orchestration contributes its actual subtask outcome to
`.ronin/provider-health.json`. A failed provider is temporarily marked
`cooling-down`; a later successful observed run clears that temporary state.
Ronin does not send synthetic pings or claim a provider is reachable without a
real outcome.

```bash
ronin util provider-health
ronin util bench --models "anthropic,gemini,ollama:qwen" --root .
ronin util scorecards import swebench-report.json
ronin util scorecards show
```

`bench` stores objective pass-rate, latency, and estimated-cost evidence as
local model scorecards. Imported SWE-bench and judge reports can add quality
evidence without copying prompt or completion text. Feed matching scorecards
into an explicit routing recommendation with:

```bash
ronin util agent-route "add retry handling" \
  --models "anthropic:sonnet,ollama:qwen" --use-scorecards
```

## Evidence-Led Model Routing

Ronin can recommend different models for different selected roles from explicit
quality/cost/latency evidence plus observed project-local provider outcomes:

```bash
ronin util agent-route "improve retry handling" \
  --models "anthropic:sonnet:0.95:0.8:0.5,ollama:qwen:0.55:0.05:0.3"
```

Write roles weight quality more heavily; exploration roles weight cost and
latency more heavily. Degraded observed providers are penalized, while unseen
providers are labelled `unknown` rather than assumed healthy. Routing prints a
roster recommendation for an explicit `--roster`; it never changes a run behind
the operator's back.

## Patch Verification and Plugins

Before an agent mutates source, Ronin parses Python, TypeScript/TSX when a
local compiler is available, JSON, and TOML. Invalid structured files are
rejected before disk mutation. This is a preflight, not a replacement for the
repository test suite.

Plugins use a literal non-executing `PLUGIN` manifest, capability metadata, and
an explicit trust gate. High-risk `subprocess` and `payment` capabilities still
need interactive approval under `--yolo`; use `ronin util plugins` to inspect
the installed surface.

## Operations View

```bash
ronin util agent-ops
```

The local terminal view joins run/queue counts, recoverable tasks, provider
health, ledger integrity, and available scorecards. It reads local state only
and makes no model or network request. The existing release automation,
reproducible package validation, Docker support, and Windows command handling
remain the release/runtime substrate for these operations.

## Competing Worktree Trials

For an important change, run explicit competing provider rosters:

```bash
ronin util trials run "add retry handling" --write \
  --candidate fast="implementer=groq,reviewer=gemini" \
  --candidate strong="implementer=anthropic,reviewer=anthropic"
```

Each team uses the normal isolated-worktree orchestration. Ronin ranks only
successful candidates whose subtasks all verified and which have no recorded
security findings. Selecting a trial is informational: no candidate diff is
merged or applied automatically.

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
fleet planning, project telemetry, local semantic fallback, sandbox fail-closed policy,
repository constitution, autonomy ledger, durable project memory, recovery,
provider-health recovery, evaluation scorecards, and structured patch checks.
Existing `ronin eval agents` continues to cover profile routing, workflow
contracts, and governance limits. For model coding quality, use the existing
SWE-bench-style and agent evaluation surfaces with real providers.
