# Phase 7 scope — which two differentiators, and why

**Status: SCOPE PROPOSAL FOR REVIEW.** Per the work order, items 1–2 are shown
before implementation.

---

## 1. Confirmed scope: items #1 and #2, in that order

**No reordering of the priority list.** #2 (unified policy engine) stays where it
is, for the reason the work order gives: it is a dependency for #6 (A2A gateway)
and it makes #3/#4 safer. The audit corroborates that from the other direction —
there is no single evaluation point today, so shipping #3 (a background-job
control plane with human takeover) or #4 (a signed extension marketplace with
scopes) on top of N independent checks would multiply the surfaces each new rule
has to be threaded through.

Building **#1 first, then #2** — not simultaneously — because #1 is where the
policy engine's hardest requirement comes from: a candidate workspace that
survives crashes and moves between local and remote workers is precisely the
thing that needs "which rules applied to *this* workspace, and can I prove it".
Designing the policy engine against a real persistence story beats designing it
abstractly and retrofitting.

## 2. Item #1 — persistent candidate workspaces: what exists, what's missing

The foundation is real, which is why this is a good first pick. A candidate is a
**detached git worktree** created by `git worktree add --detach` with versioned
JSON metadata persisted per candidate (`candidate_workspace.py:98-105`, `:46-56`,
atomically written), bound 1:1 to a mission, with code execution confined to a
locked-down Docker container and **no host fallback**. Separately, a real durable
runtime exists: `RunJournal` with `runs`/`events`/`checkpoints` tables, schema
migrations, SHA-256-verified atomic checkpoints, `resume()`, and
`interrupted_runs()` (`durable.py:204-285`).

Against the work order's four operations — snapshot, resume, compare, promote:

| Operation | Status | Evidence |
|---|---|---|
| **snapshot** | ⚠️ partial, and only as a side effect — remote-worker enqueue captures `candidates.diff()` + a SHA-256 digest into an immutable job, but it cannot be restored into a local candidate | `remote_workers.py:549-555`, `:615-645` |
| **resume** | ❌ absent for candidates. `execute_implementation` calls its runner **once** with no journal, run id, or checkpoint, so an interrupted implementation cannot resume and its partial work is unrecorded | `mission_workflow.py:130-208` |
| **compare** | ❌ absent. Nothing can diff or rank two `CandidateWorkspace`s; `worktree_trials` compares *transient* roster runs and persists nothing | `worktree_trials.py:16-83` |
| **promote** | ⚠️ stops at a local artifact — `prepare_pull_request_draft` requires approval and a named human, then writes a draft and moves to STAGING; there is no branch/commit/push | `mission_workflow.py:382-409` |

**Two structural defects worth fixing before adding features:**

1. **Crash-inconsistency by construction.** Candidate worktrees are *required* by
   a hard assertion to sit directly under the **system temp dir**
   (`candidate_workspace.py:313-320`), while their records live in `.ronin/`. A
   reboot or temp reaping deletes the checkout and leaves the record saying
   `active`. "Survives crashes" cannot be claimed until the workspace root moves
   somewhere durable.
2. **The durable runtime and the write path are mutually exclusive.** Journalled
   orchestration with `read_only=False` is *rejected outright* — it returns "use a
   mission candidate for writes" (`orchestrate.py:343-347`) — while the candidate
   write path has no journal at all. So the two halves of "durable writes" exist
   and are wired to refuse each other. Connecting them **is** item #1's core.

Also missing and cheap: `interrupted_runs()` has **no caller** outside its module
(`durable.py:319`), so `--resume-run <id>` requires the operator to already know
the id — there is no way to list what is resumable.

**Proposed #1 deliverables:** durable workspace root; journal + checkpoint around
the candidate implementation turn (removing the read-only gate); a real
snapshot/restore primitive that round-trips locally; `candidate compare` over
persisted candidates; promote that branches/commits/pushes behind the existing
human-approval gate; and a `resumable` listing command. Every one of these is
testable offline.

**Explicitly deferred:** live status streaming and stop/pause/resume verbs are
**item #3**, not #1 — no live stream (SSE/websocket) exists for missions or
candidates and `MissionStage` has no paused/stopped state
(`apps/api/csk_api/main.py:183-213`). Recording that here so #1 does not quietly
absorb #3's scope.

## 3. Item #2 — unified policy engine

Design, reference notes, and the enumeration of today's N independent checks are
in **`docs/design/policy-engine-design.md`**. Summary of why it is urgent: the
authorization surface audit found the decision logic for path, command, network,
MCP capability, provider, cost, reviewer, and deployment rules spread across
independent modules with **no single evaluation point**, three disjoint capability
vocabularies, pack policy files that are declared but **never parsed**, and MCP
tool calls that are **never audit-logged**. A per-domain-pack rule today has
nowhere to live.

## 4. Definition of done for this phase

Per the work order: exact pass/fail counts; a demonstration — test or script — of
the policy engine **denying a real unsafe action end-to-end across at least two
different domain packs** (not unit-testing rule matching in isolation); and
docs/CHANGELOG updated. The two-pack requirement has a prerequisite worth
flagging now: a *second* usable pack has to exist for that demonstration, and
today only the coding pack has real tools behind its declarations
(`education`/`healthcare` declare tools that do not exist). Either the research
pack lands first, or the demonstration uses coding + a purpose-built test pack —
your call.

---

**Review asks.** (1) Confirm #1 then #2, sequential, not parallel. (2) Approve
moving the candidate workspace root out of the system temp dir — it changes an
existing hard assertion and any on-disk records referring to old paths.
(3) For the two-pack policy demonstration, use coding + research (blocking on the
research pack) or coding + a test-only pack?
