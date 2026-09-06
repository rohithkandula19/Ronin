# Retainer — Ronin as a standing teammate

A *ronin* is a masterless swordsman: summoned, paid, gone. Everything in this
repository is built that way. You open a terminal, the agent exists; you close it,
it does not. A *retainer* is the opposite figure — the same swordsman kept in
permanent service to a house, with a post, standing orders, and the authority to
act without being asked each time.

This document is the contract for building that second figure on top of the first.
It is written the way `docs/ARCHITECTURE.md` was written: **before** the
implementation, so the boundaries are decided rather than discovered.

The prompt for it was xAI's Grok Bot (announced 2026-08-14): always-on AI
teammates, each with its own cloud computer, reachable in chat, taught by
demonstration, and left running on a schedule. §1 records what that product
actually is, because half of the design here is a deliberate disagreement with it.

---

## 0. Summary in ten lines

- A **Retainer** is a durable record, not a running process. Nothing is
  always-on; the *availability* is always-on.
- **Standing orders compile to policy.** Prose goes in the prompt; enforcement
  goes through `PolicyEngine` and the parsed-command denylist. Grok Bot's
  boundaries are prose. Ours are not, and that is the whole differentiator.
- Two Retainers **are** separate security boundaries. Grok Bot's own
  documentation says two Bots are not.
- **Escalation is a durable channel**, not a modal dialog. A run that needs a
  human ends; the reply resumes it; the preconditions are re-checked on resume.
- Credentials never enter the workspace. The egress proxy holds them.
- Routines and mentions travel the **same** summons path, so a routine can never
  do something a mention could not.
- Every outward effect is idempotent by construction, because webhooks redeliver
  and resumes replay.
- Adapters are thin. The core knows nothing about GitHub, Slack, or anything else.
- Most of the engine already exists (§5). Seven components do not (§6).
- Five decisions are yours before any of it is built (§7).

---

## 1. What Grok Bot actually is

Recorded from first-party sources so the design disagrees with the real thing and
not a caricature.

**Object model.** *"The main objects in Grok Bot are Bots, not conversations."*
A Bot owns Chats, Prompts, Tools, and Artifacts. Bots are invoked by `/` skills
and driven by Routines (scheduled or event-triggered). "Teach a task" records up
to ten minutes of browser interaction and emits a *draft* skill. Ceilings: 50
Bots plus group chats per account, 50 routines per Bot, 20 retained run records.
A "test run" does the real work — there is no dry run.

**Runtime.** One persistent Firecracker microVM **per user**, shared by every Bot
that user owns. `/workspace` is durable; browser cookies persist between runs.
Tool preference is ordered: structured plugin → official API or CLI → cloud
browser → the local computer.

**Authority.** Approvals are Allow-once or Deny, and the documentation is candid:
*"An approval controls the proposed action. It does not reverse work already
completed."* Boundaries are set **in prose**. The single enforced layer is Auto
Review, a model that evaluates shell commands, plugin calls, computer use,
automation writes and delegation before execution — and which *"does not review
every side effect."*

**The admission that shapes this document.** xAI publishes, in its own security
notes, that *two Bots are not separate security boundaries* and that you should
*not use separate Bots as a security boundary.* One VM, one cookie jar, one
credential set, N personas.

**Metering.** Agent steps and tokens, not messages. Each bot-to-bot message runs
a billable turn. The community forum records a user's four specialist Bots
talking to each other after being told to stop and consuming **100% of a weekly
allowance in 21 seconds**. Staff response: use one command agent with subagents,
which terminate, rather than standing peers, which do not.

**Developer surface.** There is no public API to create or drive a Bot. What xAI
ships instead is Grok Build, an open-source coding-agent harness exposed over the
**Agent Client Protocol** — the same ACP this repository already speaks in
`src/ronin/cli/acp.py`.

**Field reports.** What works: cross-app reach where no integration exists,
persistent signed-in sessions, multiple accounts per connector, near-zero setup.
What breaks: loops, CAPTCHA and MFA walls, plugin disconnections, runaway usage,
and a staff-confirmed stuck-computer state. The consensus operating pattern from
people who use it daily: *one-time task → corrected task → saved skill → tested
routine.* Never automate on the first attempt.

---

## 2. The object model

Seven records. Everything else in this document is machinery for moving them.

| Record | What it is | Lifetime |
|---|---|---|
| **Deployment** | One place Retainers run, and the capabilities that place holds. | Durable. Operational, not per-Retainer. |
| **Retainer** | A named identity with standing orders, a post, skills and routines. | Durable. Edited by a human. |
| **Post** | The workspace a Retainer holds — a repo checkout or worktree, plus its `.ronin/`. | Durable. Rebuildable from git. |
| **Standing Orders** | The compiled authority: allowed tools, policy ruleset, escalation rules, budgets. | Durable. Versioned; a change is an audit event. |
| **Summons** | One normalised request to act — from a mention, a routine, or a resumed escalation. | One turn's worth. |
| **Escalation** | A persisted request for human authority, with the state to resume from. | Until answered or expired. |
| **Effect** | One outward, non-idempotent act: a comment, a push, a request with a body. | Recorded forever in the ledger. |

The deliberate inversion of Grok Bot: **there is no VM record**, because there is
no VM. A Post is a directory and a git remote. If it is lost, it is re-cloned.

---

## 3. The one hard problem: authority without a human

Everything else here is plumbing. This is the design.

Ronin's current answer to "nobody is attached" is `UnattendedAsker` — it denies
(`safety/policy.py:481`, `core/types.py:667` `DENY_UNATTENDED`). That is exactly
right for a terminal and fatal for a teammate: a retainer that can only do what
needs no permission cannot be trusted with anything, because it was never trusted
with anything.

The fix is not to relax the denial. It is to give authority **three** shapes
instead of two.

### 3.1 Pre-filter — never offer what can never be allowed

`src/ronin/cli/serve.py:265-296` already does this: it calls `policy.relaxes(spec)`
**before publishing a tool** over MCP. A Retainer publishes its tool surface the
same way. A tool the standing orders can never permit is not denied at call time;
it is absent, and the model never proposes it. This is the cheapest possible
safety layer and it is already written.

### 3.2 Standing authority — compiled, not described

A Retainer's standing orders compile into two artefacts:

- a `PolicyEngine` ruleset, and
- a tool allowlist passed to `Agent.open(tools=…)`.

The prose version also goes into the prompt, because the model needs to know its
own remit. **The prose is never the enforcement.** Under it sits the layer Grok
Bot does not have: the unconditional denylist over *parsed command segments*
(`src/ronin/safety/command.py`, `src/ronin/safety/denylist.py`), which understands
that `awk 'BEGIN{system("...")}'`, `flock -c`, `sed` with an `e` command and
`timeout 5s sudo …` are all command execution, and that `curl -T` and a `>`
redirect are both writes. Fourteen merged changes went into making that layer
agree with the real binaries. This is where they pay off.

Consequence, stated as a guarantee: **two Retainers are separate security
boundaries.** Different ruleset, different post, different credentials at the
proxy. This is the single largest architectural difference from the product being
matched, and it exists because the enforcement is deterministic rather than a
model's opinion.

### 3.3 Escalation — durable, not modal

When a Retainer needs authority it does not have:

1. The `Asker` implementation records an **Escalation**: the `ApprovalRequest`,
   the current `AgentState`, and a checkpoint id.
2. The run **ends** — `exit_code 2`, which `ui.headless.exit_code_for` already
   means as *an approval was requested and denied*.
3. The adapter posts the request into the thread it came from, in the operator's
   words, using the `feedback` / `detail` split the `Asker` protocol already
   draws (`safety/policy.py:429-452`).
4. A human answers, minutes or days later. The answer is a new **Summons**.
5. On resume, **the preconditions are re-validated.** `verify/checkpoints.py`
   gives `session_diff(base=…)`, so the reply can be answered with *what changed
   since you were asked* rather than a silent replay against a moved base.

Nothing is held open. No process waits. This falls straight out of §4's rule that
a Retainer is a record, and it is the only version of a multi-day approval pause
that survives a restart.

Grok Bot's own caveat applies to us unchanged, and belongs in the operator-facing
copy verbatim: **an approval controls the proposed action; it does not reverse
work already completed.**

---

## 4. The shape of the system

```
┌─ ingress ──────────────────────────────────────────────────────┐
│ github app  ·  slack  ·  schedule  ·  escalation reply         │
│ verify signature → parse mention → normalise to a Summons      │
└───────────────────────────┬────────────────────────────────────┘
┌─ retainer plane ──────────▼────────────────────────────────────┐
│ registry   standing orders → PolicyEngine ruleset + toolset    │
│ threads    external thread id ⇄ ronin session id               │
│ ledger     effect idempotency  (retainer, summons, step, hash) │
│ escalations  persisted asks + the state to resume from         │
│ budgets    steps · tokens · wall clock · notifications/day     │
└───────────────────────────┬────────────────────────────────────┘
┌─ engine — ALREADY EXISTS ─▼────────────────────────────────────┐
│ cli.sdk.Agent → cli.stream.Conversation → core.loop.run_turn   │
│ resume · compaction · checkpoints · transcript · gate · policy │
└───────────────────────────┬────────────────────────────────────┘
┌─ egress ──────────────────▼────────────────────────────────────┐
│ credential injection proxy — placeholders in, secrets out      │
│ destination allowlist · effect ledger check · audit log        │
└────────────────────────────────────────────────────────────────┘
```

Three rules make the diagram load-bearing:

1. **The engine layer imports nothing from the retainer plane.** `run_turn` has
   zero UI and zero provider knowledge today and keeps it.
2. **Every arrow crossing into egress passes the ledger.** No outward effect is
   emitted twice for one `(retainer, summons, step, content-hash)`.
3. **The ingress layer produces exactly one type.** A routine firing and a human
   typing `@ronin` produce the same `Summons`. There is no privileged path.

---

## 5. What already exists — do not rebuild it

Verdict from a full read of the tree.

| Component | Verdict |
|---|---|
| `core/loop.run_turn` | **Reusable as-is.** No UI, no provider knowledge, by construction. |
| `core/types`, `core/protocols`, `core/steering` | **Reusable as-is.** Logic-free contract; imports nothing from Ronin. |
| `cli/stream.Conversation` | **Reusable as-is.** No terminal anywhere; it is in `cli/` for layering, not for a TTY. |
| `cli/spine` (`Paths` / `Loaded` / `Runtime`) | **Reusable as-is.** `home` and `cwd` are injected, never process-discovered. |
| `cli/wire.build_runtime` | **Reusable as-is.** `extra_tools=` folds extras *below* the gate — the seam for a reply tool. |
| `cli/sdk.Agent` | **Reusable as-is. Start here.** `asker=`, `tools=`, `resume=`, `home=` are all parameters already. |
| `safety/*` | **Reusable as-is.** `UnattendedAsker` denying by default is the correct floor to build up from. |
| `persistence/*` (transcript, index, resume) | **Reusable as-is.** `replay()` → `Conversation.resume_from(state)` is the whole restore half. |
| `context/*`, `verify/*`, `providers/*`, `tools/*`, `cli/gate` | **Reusable as-is.** |
| `ui/reduce`, `ui/render`, `ui/headless` | **Reusable as-is.** Pure, stdlib-only, no terminal import. |
| `cli/http_api` | **Extend.** `route()` is socket-free and total; it lacks SSE and thread state. |
| `cli/serve`, `cli/acp` | **Copy the pattern.** They are the existing "no human on stdin" precedent. |
| `ext/plugins` | **Cannot host this.** Plugins load no Python — five declarative surfaces only. |
| `ui/app` (Textual) | **Terminal-coupled** — but its `Session` callbacks are the honest checklist of what "attended" means. |
| `cli/main` | **Terminal-coupled.** `_api` / `_acp` / `_mcp_serve` show the wiring to copy. |

The new code goes in `src/ronin/retainer/` with its wire surface at
`src/ronin/cli/retain.py`, beside `http_api.py`, `serve.py` and `acp.py`. That is
the tree's own stated precedent: *the CLI drives `cli.sdk.Agent`, which is the
CLI's job and not the core's.*

---

## 6. What is genuinely new

Seven components. Nothing else.

1. **Ingress** — receive, verify signature, parse the mention, emit a `Summons`.
2. **Reply channel** — a gated tool passed through `extra_tools=`, or a post-run
   call on `AgentResult.text`. Gated either way; a reply is an effect.
3. **`ThreadAsker`** — the escalation `Asker` of §3.3.
4. **Post mapping** — repo → workspace (clone or worktree). `Paths` assumes a
   local directory exists; something has to make one.
5. **Thread ⇄ session persistence** — `persistence.resume` gives the replay half;
   nothing today maps an external thread id to a session id.
6. **Effect ledger** — `(retainer, summons, step, content-hash)`. Webhooks
   redeliver and resumes replay; without this, a resumed run comments twice.
7. **Egress credential proxy** — the workspace holds placeholders; the proxy,
   outside the workspace, substitutes real secrets per approved destination. This
   extends a discipline the repo already keeps inward: `ModelSpec.api_key_env`
   names a variable and `load_workspace(environ=…)` defaults to **nothing**, so
   an unset variable is a named config error rather than an empty token quietly
   shipped to a server.

---

## 7. Decisions

`RONIN.md`: *if a design decision has two reasonable options, stop and ask me, do
not build both.* Five qualified. Two are answered; three are not yet needed and
are asked at the step that needs them.

**1. Where does a Retainer run? — Both.** A daemon on your own machine and a
hosted one, the operator's choice per deployment. That answer has a consequence
the design has to carry rather than remember: *Amazon v. Perplexity* (9th Cir.,
2026-08-04) held that the CFAA's safe harbour reaches only agents whose
communications pass through **the user's own computer**, so browser-driving
third-party sites is defensible locally and is not defensible from a server.

So **browser use is a property of the deployment, not of the Retainer**. A
`Deployment` refuses to hold `Capability.BROWSER` when it is hosted, and standing
orders can only ever *request* a capability — `StandingOrders.granted()`
intersects the request with what the deployment actually has. Moving a Retainer
from a laptop to a server quietly narrows what it can do; it cannot be argued
into the capability by its own orders, and nobody has to remember the case law at
the point of use.

**2. Which adapters? — All three.** GitHub, Slack and Telegram. This does not
change the build order; it splits step 7 into 7a/7b/7c. The core stays
platform-agnostic, which is exactly what makes the second and third adapter cheap
instead of three times the work. GitHub lands first as the reference adapter,
because the escalation and reply paths have to be proven against one real surface
before the other two are worth writing.

**3. Where does an escalation land, and who may answer? — In the thread, and
anyone with write access.** These two turned out to be one decision. If people
other than the owner may answer, the request has to be *visible* to them, so the
full ask goes in the thread where the work is; a private notification goes to the
owner as well, so it does not sit unseen. "Detail in private" and "collaborators
may answer" cannot both be true.

The consequence is that authority now depends on an identity, and the identity
must be **verified by the adapter** — never `Summons.actor`, which is a display
name an external system supplied. `EscalationStore.answer` takes the verified id
and a predicate; it defaults to letting nobody answer, so a caller that forgets
to pass one gets the narrow rule rather than an open door.

**Still open, asked when they bite:**

4. **Post granularity:** one workspace per Retainer, or one per repo per
   Retainer? The second is more isolation and more disk. Needed at step 8 —
   the thread map already stores a workspace per binding, so both shapes are
   representable and nothing before then has to assume one.
5. **Whose hands does it act with?** Its own bot identity, or yours? A separate
   identity means separate permissions and a clean audit trail; yours means it
   can do exactly what you can, which is both the convenience and the problem.
   Needed at step 7a. The `Retainer.acts_as` field represents both without
   deciding.

---

## 8. Build order

One change at a time, each green before the next, as with the safety campaign.
Test files follow the tree's convention — `tests/retainer/test_retainer_<topic>.py`,
with one `tests/retainer/retainer_harness.py`, because the test trees are
deliberately not packages and every module shares one flat namespace.

| # | Change | Why it is first |
|---|---|---|
| 1 | `retainer/model.py` — the six records | Pure frozen dataclasses, no I/O. The contract, testable alone. |
| 2 | `retainer/orders.py` — orders → ruleset + toolset | Authority before anything can act. Reuses `policy.relaxes`. |
| 3 | `retainer/ledger.py` — effect idempotency | Must exist before the first outward effect, not after the first duplicate. |
| 4 | `retainer/threads.py` — thread ⇄ session | The missing half of resume. |
| 5 | `retainer/ask.py` — `ThreadAsker` + escalation records | §3.3, still with no network anywhere. |
| 6 | `cli/retain.py` — the wire surface | Mirrors `http_api.py`: a total, socket-free `route()` plus signature checks. |
| 7a | `retainer/adapters/github.py` — mention in, comment out | The reference adapter, once everything it needs is proven. |
| 7b | `retainer/adapters/slack.py` | Second adapter. Proves the core is really platform-agnostic. |
| 7c | `retainer/adapters/telegram.py` | Third adapter. Not the v1 `packages/cli` bot, which is v1 code. |
| 8 | `retainer/posts.py` — repo → workspace | Clone or worktree. Decision 4 is answered here, not before. |
| 9 | `retainer/routines.py` — the scheduler | Emits the same `Summons`. Last, because it multiplies whatever is already wrong. |

The egress proxy (§6.7) is deliberately outside this list. It is a separate
process and quite possibly not Python in this repository; it should not block the
plane above it, and the plane above it must work with the proxy absent by simply
having no credentials to offer.

---

## 9. Failure modes this design is aimed at

Named from measured evidence, each with the mechanism that answers it.

| Failure | Evidence | Answer here |
|---|---|---|
| Agents lose the plot on long horizons | METR's 80% time-horizon is 4–6× shorter than its 50% horizon; reliability falls below 10% past roughly four human-hours | Bounded runs. `max_iterations`, budget guard and stall detection already exist; a Retainer sets them low and escalates rather than pushing on. |
| Meltdown loops | Vending-Bench: agents lose the plot rather than failing a step | The run *ends* at an escalation. Nothing waits in a loop for an answer. |
| Runaway spend from standing peers | 100% of a weekly allowance in 21 seconds, four Bots talking | **No Retainer-to-Retainer messaging in v1.** Subagents terminate; standing peers do not. |
| Duplicate effects | Webhook redelivery, resume replay | The ledger. |
| Prompt injection from the thread | The lethal trifecta: private data, untrusted content, external communication | Untrusted content is already tainted and gated. The reply tool is an effect and passes the gate like any other. |
| Notification fatigue | The practical ceiling is roughly 3–5 notifications per person per day | Acting proactively and *notifying* proactively are separate budgets. Finishing quietly and leaving a draft PR beats a ping. |
| Skills rotting silently | Tool-schema drift breaks recorded routines | A routine records the tool schemas it was built against and escalates on drift instead of improvising. |

---

## 10. Non-goals for v1

- **No cloud computer.** No VM, no persistent browser profile, no computer use.
  Decision 7.1 governs whether that ever changes.
- **No teaching by demonstration.** Skills already exist in this repo as files
  (`ext/skills`); recording a browser is a different product.
- **No Retainer-to-Retainer messaging.** See §9.
- **No customer-facing or regulated use.** No rehearsal mode, no confidence
  thresholds, no response-level audit export. Until those exist, this is an
  internal teammate.
- **No new dependencies.** `RONIN.md` says prefer stdlib and justify each one in
  a line; `cli/http_api.py` proves a wire surface needs none.
