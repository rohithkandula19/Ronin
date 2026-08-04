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

## Persistent Role Teams

`ronin util team` gives the core architect, implementer, reviewer, tester,
security, and release roles stable project-local identities. The SQLite
supervisor persists lifecycle state, in-flight mission assignment, health,
restart count, role experience, and a compact hash-chained audit under
`.ronin/persistent-agents.sqlite`.

```bash
ronin util team init
ronin util team status
ronin util team assign architect-01 mission-20260804-120000-abcdef "Design the retry boundary"
ronin util team start architect-01
ronin util team heartbeat architect-01
ronin util team complete architect-01 --summary "Recorded a bounded design proposal."
ronin util team release architect-01
ronin util team audit
```

The lifecycle is explicit: `idle -> assigned -> running -> completed -> idle`.
An assigned or running role with a stale heartbeat is recorded as crashed. The
supervisor can return it to `assigned` with the same mission and task plus a
restart count, so a governed worker can recover from durable state rather than
inventing progress. `team supervise` manages that recovery boundary; it does
not launch a hidden daemon, provider, shell command, or code-editing worker.

Each role keeps an attributed experience ledger with source type/id, confidence,
expiry, access count, and last-accessed timestamp. Likely secrets are refused.
Workarounds default to a 30-day lifetime, high-confidence ADRs to one year, and
other entries to 90 days. `team memory compact` archives expired
low-confidence entries while retaining a bounded historical summary:

```bash
ronin util team memory remember reviewer-01 \
  "FastAPI route changes require matching OpenAPI coverage." \
  --source-type pull_request --source-id '#123' --confidence 0.9
ronin util team memory recall reviewer-01 "FastAPI route coverage"
ronin util team memory compact reviewer-01
```

Before a role starts, `team context` assembles a local, token-bounded context
pack from compatible project instructions, BM25-ranked repository files and
tests, and the active role's recalled experience. The pack uses references and
symbol outlines, not raw chat history. It truncates visibly when its token
budget is exhausted and never calls a provider.

```bash
ronin util team context tester-01 "add retry coverage" --mission mission-20260804-120000-abcdef
```

Mission Control exposes only safe team metadata: role identity, lifecycle,
mission id, restart count, and health timestamp. It deliberately excludes task
text, operator summaries, scratchpads, and experience contents.

## Mission Foundation

Ronin's issue-to-PR foundation uses structured artifacts rather than an
unbounded chat transcript. A mission holds the inspected issue intent,
planning artifact, independent test report, review findings, security scan,
budget ceiling, and an append-only local audit chain.

```bash
ronin util mission create "Harden retry behavior" \
  "Retry transient transport failures and add regression coverage." \
  --source github --source-id 123 --acceptance "Retries stop after the configured limit."
ronin util mission list
ronin util mission advance mission-20260802-120000-abcdef inspecting
ronin util mission audit mission-20260802-120000-abcdef
```

The deliberate state path is `pending -> inspecting -> planning ->
implementing -> testing -> reviewing -> security -> awaiting_approval ->
staging -> completed`. A failed implementation or test may return to its
permitted earlier planning or implementation state; invalid shortcuts are
rejected. The mission store persists a mutable typed snapshot under
`.ronin/missions/` and a hash-chained `.events.jsonl` file alongside it.
`mission audit` verifies both the chain and that its final event still matches
the snapshot. These commands only record operator evidence; they do not call a
provider, run a shell command, edit code, or create a pull request.

Each mission has hard token, cost, wall-clock, tool-call, concurrency, and
repair-attempt ceilings. The new contract makes those values durable before an
issue worker is attached, so the eventual worker cannot reinterpret a mission
as an unbounded autonomous task. Existing installations require no migration:
all mission records are project-local and removable with `.ronin/` metadata.

### Typed Mission Event Bus

Every committed mission audit record also emits one or more schema-first,
idempotent event envelopes under `.ronin/mission-events/`. The local durable
bus is a portable transport boundary for future NATS, Redis Streams, or RabbitMQ
adapters; the hash-chained mission audit remains the source of truth.

```bash
ronin util mission events list
ronin util mission events list --mission mission-20260803-120000-abcdef
ronin util mission events verify
ronin util mission events replay mission-20260803-120000-abcdef
```

Topics include `mission.created`, `mission.transitioned`, `agent.assigned`,
`handoff.completed`, `test.passed`, `test.failed`, and `policy.violation`.
Envelopes carry a schema version, correlation/causation identifiers, producer,
idempotency key, state metadata, and artifact digest only. They deliberately
exclude issue bodies, artifact content, paths, credentials, and raw worker
output. `replay` backfills previously recorded audit events safely: the
idempotency key ensures it cannot duplicate delivery.

### Candidate Workspaces

Before an implementation is trusted, create a disposable candidate checkout:

```bash
ronin util mission workspace create mission-20260802-120000-abcdef \
  --image python:3.14-alpine
ronin util mission workspace list
ronin util mission workspace diff candidate-20260802-120010-fedcba
ronin util mission workspace run candidate-20260802-120010-fedcba "pytest -q" --yes
ronin util mission workspace destroy candidate-20260802-120010-fedcba --yes
```

Creation uses a detached Git worktree at the committed source revision, leaving
the caller's checkout untouched. A candidate may be inspected and diffed, but
it never executes on the host: `workspace run` requires the image supplied at
creation and uses Docker with dropped capabilities, `no-new-privileges`, PID
and memory limits, a candidate-only bind mount, and no network. There is no
host fallback. Destruction removes only that detached candidate checkout after
an explicit `--yes`; its terminal metadata remains under
`.ronin/candidate-workspaces/` for auditability.

`ronin ui` exposes mission stages, audit status, evidence verdicts, and safe
candidate metadata in its read-only Operations tab. It intentionally does not
publish issue bodies, artifact content, or filesystem paths through the status
API.

### Evidence-Gated Issue To PR

The single-mission path is now executable as a sequence of typed evidence
gates. It never treats an agent claim or a green-looking terminal line as proof:

```bash
ronin util mission plan mission-20260802-120000-abcdef \
  --step "Implement a bounded retry path" --file src/client.py \
  --test-addition tests/test_client.py --rollback "Remove the retry branch"

# An implementation agent works only in the detached candidate checkout.
ronin util mission verify mission-20260802-120000-abcdef "pytest -q" --yes
ronin util mission review mission-20260802-120000-abcdef
ronin util mission security mission-20260802-120000-abcdef
ronin util mission evaluate mission-20260802-120000-abcdef
ronin util mission draft-pr mission-20260802-120000-abcdef \
  --approved-by "Rohith" --yes
```

`plan` turns a `MissionSpec` into a typed `PlanArtifact` and advances only to
candidate implementation. `verify` executes only in the attached Docker
candidate and records the real exit status, duration, cumulative tool use, and
repair count. A failed command can return to implementation only while the
configured repair budget remains; a blocked Docker command, timeout, or budget
exhaustion fails the mission rather than pretending it passed.

`review` is a deterministic diff-hygiene gate: it requires a non-empty diff,
checks Git whitespace, and flags unresolved conflict markers, debug leftovers,
and high-severity diff guard findings. `security` scans candidate-added lines
for credentials and private-key material using masked evidence only; a finding
returns the mission to implementation without retaining the secret value.

`evaluate` records a release gate over the structured plan, active candidate,
non-empty diff, passed Docker test, approved review, passed security scan,
budget, and audit chain. `draft-pr` needs that gate plus `--yes` and a named
human approver. It creates a **local** title/body/branch proposal under the
mission record and advances to `staging`; it does not create a branch, commit,
push, or GitHub/GitLab pull request. Remote publication remains an explicit
integration step, so a verified draft cannot silently ship code.

### Remote Issue Intake

Create the same bounded, auditable mission from one typed remote issue reference:

```bash
# Requires `gh auth login`; reads one issue through the authenticated gh CLI.
ronin util mission import github owner/repository#123

# Requires GITLAB_TOKEN or GITLAB_PERSONAL_ACCESS_TOKEN in the environment.
ronin util mission import gitlab group/project#456
```

References are strict (`owner/repository#number` for GitHub and
`group/project#number` for GitLab). Import stores the source, canonical
reference, repository, title, bounded issue body, labels, and a source-context
link in the local mission record; it starts no agent, candidate workspace, or
command. GitHub uses the existing authenticated `gh` CLI. GitLab accepts only
HTTPS endpoints and reads its token from the process environment without
persisting or rendering it. For a self-hosted instance, point
`--gitlab-url` only at an HTTPS GitLab endpoint you trust.

Mission Control displays the imported source reference alongside the existing
read-only audit and evidence state. It continues to omit issue bodies and
tokens.

### Sandboxed Remote Verification Workers

Ronin can hand a candidate verification to a separately operated worker without
giving it the controller checkout, Git credentials, approval authority, or any
path to publish code:

```bash
# Run once on the controller. The token is shown once; Ronin stores only a digest.
ronin util mission worker auth-init --yes

# Snapshot the active candidate's current patch for a Docker test job.
ronin util mission worker enqueue mission-20260803-120000-abcdef "pytest -q" \
  --repository-url https://github.com/owner/repository.git

# On a trusted worker with the token in its environment.
RONIN_WORKER_TOKEN='...' ronin util mission worker run \
  --endpoint https://controller.example --worker-id build-east-1 --yes
```

The worker endpoint authenticates with the controller token, then issues a
one-time lease token and immutable dispatch: credential-free HTTPS clone URL,
base revision, bounded patch, Docker image, command, and timeout. The worker
clones into a temporary directory, applies the patch with Git, and runs only
the configured command in Docker with no network, dropped capabilities,
`no-new-privileges`, PID/memory limits, and a disposable workspace bind mount.
Only exit status, duration, and a bounded status summary return to the
controller; raw logs do not.

Before accepting completion, the controller rechecks that the candidate's
mission, base revision, and patch digest are unchanged. A changed candidate,
expired lease, wrong worker, or wrong one-time token cannot advance the mission.
Passed remote evidence uses the same budgeted test gate as local verification;
failed evidence returns to implementation or fails within the existing repair
limits. Remote workers cannot stage, commit, push, create a PR, or approve a
release. Keep a non-local controller behind HTTPS; HTTP is accepted only for
localhost development.

## Fleet Execution

Turn an approved saved plan into an explicit, durable local run:

```bash
ronin util fleet start fleet-20260731-120000-abc123
ronin util fleet runs
ronin util fleet run-next fleet-run-20260731-120500-def456
```

`run-next` claims exactly one dependency-ready wave, runs only the specialists
saved in that wave, and persists the resulting agent run id, proposal id, and
terminal status under `.ronin/fleet-runs/`. It does not create a daemon or run
an unbounded backlog. The run's saved parallelism remains the hard maximum for
that wave.

Only implementation waves in a write plan receive isolated write worktrees;
their result is still a retained proposal that must be reviewed and explicitly
staged with `ronin util proposals apply --yes`. Research and acceptance waves
are read-only. Fleet execution never stages, commits, pushes, resolves a
conflict, or auto-merges code.

After investigating a failed wave, make it eligible for another explicit worker
claim:

```bash
ronin util fleet retry fleet-run-20260731-120500-def456 2 --yes
```

If a worker process has definitely stopped before recording a terminal result,
release only its active claim:

```bash
ronin util fleet recover fleet-run-20260731-120500-def456 --yes
```

Recovery is intentionally explicit because releasing a still-running worker
could duplicate provider work. `ronin ui` exposes fleet plans, fleet-run state,
and proposals as read-only local operational data.

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
