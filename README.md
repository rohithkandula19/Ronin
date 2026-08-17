# ronin 🐼

> **The coding agent that runs free, local, and air-gapped.** ronin reads, edits, and runs your code from the terminal — Claude-Code shaped, but masterless: bring **any provider** (Claude for top quality, free tiers on Gemini / Cerebras / Groq / OpenRouter, or a fully local model with zero keys), keep **everything on your machine**, and put a **hard safety floor** under every destructive command.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-v1.0.0-blue)](CHANGELOG.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-9971%20passing-brightgreen.svg)](#-whats-under-the-hood)
[![Providers](https://img.shields.io/badge/providers-Claude%20·%20Gemini%20·%20Cerebras%20·%20Groq%20·%20OpenRouter%20·%20Ollama%20·%20OpenAI-d4a373)](#-supported-providers)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Why not just Claude Code?**

- **Works offline / air-gapped** — `ronin --offline` forces a local brain and strips every network tool; nothing leaves your machine. `ronin util local` runs a fully local open model with **zero API keys**.
- **No telemetry unless you turn it on** — off by default, and off is the state you stay in if you ignore this line. `ronin util privacy` audits what's stored locally; `ronin telemetry status` says whether anything is being sent and `ronin telemetry show` prints every payload that ever left, verbatim. See [Telemetry](#telemetry--off-by-default-auditable-when-on).
- **Any provider, no lock-in** — the same agent runs on Claude, Gemini, Cerebras, Groq, OpenRouter, OpenAI, or Ollama. Free tiers work; no credit card required.
- **A destructive-command floor** — `rm -rf`, force-pushes, and friends are hard-blocked below the approval layer, with a drift guard so config edits can't silently weaken it.
- **Evidence-gated engineering missions** — turn a GitHub or GitLab issue into a bounded, auditable issue-to-PR workflow with disposable Docker candidates, independent gates, and explicit human approval before staging.

```bash
$ curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
$ ronin1 code "fix the failing test"           # the coding agent
$ ronin1 code "explain @main.py and add tests" # @-mention files inline
$ ronin1 --offline                             # air-gapped: local brain, zero egress
```

> **Two commands, and which one you want depends on which tree you want.** This README
> documents the **v1** CLI (`packages/cli`), whose binary is now **`ronin1`** — renamed
> from `ronin`, because console scripts are not namespaced and two distributions
> claiming one name means whichever was installed second silently wins. Most command
> examples below still read `ronin …`; substitute `ronin1`.
>
> **`ronin`** (and its long-standing alias **`ronin2`**) is the **v2** tree at
> `src/ronin` — a smaller, strictly-typed rebuild with its own docs in
> [docs/site/quickstart.md](docs/site/quickstart.md). Its verbs are not v1's: a bare
> prompt, `-p`, `doctor`, `sessions`, `export`, `eval`, `duel`, `telemetry`, `mcp-serve`.
> The two ship side by side on purpose until `tests/evals/` has measured both.
>
> The two-letter `ro` alias is gone: `ro` meaning v1 while `ronin` means v2 is the same
> silent swap in miniature.
>
> **Platform support:** macOS and Linux are supported. Windows is supported via **WSL** (run the same install command inside a WSL shell) — native Windows is not yet supported. Requires Python 3.11+ and `git`.

## 🖥 Ronin AI OS — the web experience

The terminal agent is the product; this is its **companion web experience** —
policy-bounded workspaces (coding, research, healthcare, education) on the same
provider-agnostic runtime, useful when you want a UI instead of a shell.

**▶ Live:** **https://ronin-ai-os-staging.vercel.app** — open **`/os`** for Ronin Home,
or jump straight into a world:

| World | Route | Posture |
| :--- | :--- | :--- |
| **Coding** | [`/os/code`](https://ronin-ai-os-staging.vercel.app/os/code) | Plan-first IDE over the real runtime — files, diffs, tests, approval-gated writes |
| **Research** | [`/os/research`](https://ronin-ai-os-staging.vercel.app/os/research) | Source-first notebooks with claim-to-source mapping; never invents a citation |
| **Healthcare** | [`/os/healthcare`](https://ronin-ai-os-staging.vercel.app/os/healthcare) | Educational, **non-diagnostic** health information with an emergency boundary |
| **Education** | [`/os/education`](https://ronin-ai-os-staging.vercel.app/os/education) | Role-aware tutoring and practice, grounded in sources, fail-closed on graded work |

The worlds connect to a live FastAPI backend (`/api/v1`) when one is reachable, and
degrade honestly to a labelled offline sample otherwise — a **Live · API** / **Offline ·
sample** badge always tells you which.

**Under the hood** — a pnpm/Turborepo workspace in [`apps/web`](apps/web) (Next.js 16 +
React 19 + Tailwind v4) and [`apps/api`](apps/api) (FastAPI), built on the **Ronin Design
System** ([`packages/design-system`](packages/design-system), RDS 1.0 — the "Sumi" ink
identity: warm paper/clay palette, four themes, self-hosted Inter / Fraunces / JetBrains
Mono type).

```bash
pnpm install
pnpm --filter @ronin/web dev        # → http://localhost:3000  (landing + /os)
uv run --package ronin-api uvicorn csk_api.main:app --reload   # the /api/v1 backend
```

Deploying the backend live? See [`docs/beta/deploy-backend.md`](docs/beta/deploy-backend.md).

## 🎬 Demo

Usage dashboard → gamified profile → the 31-game arcade, in one shot:

![ronin demo](assets/ronin-demo.gif)

Runs **free**: configured on Cerebras's free tier, answering a real question for **$0**:

![ronin runs free](assets/ronin-free.gif)

The animated panda mascot, the command surface, and live MCP wiring:

![ronin mascot](docs/demo/ronin.gif)

`ronin util image "a red panda samurai, neon, flat vector"`: **free, no API key**, generated and shown right in your terminal:

![example image generated by ronin](docs/demo/example-image.png)

Regenerate the walkthrough anytime with [`vhs`](https://github.com/charmbracelet/vhs): `brew install vhs && vhs docs/demo/demo.tape`.

## What is `ronin`?

**One front door:** type **`ronin`** and you get a single agent that reads, writes, and runs code (every edit and shell command gated behind a diff preview and your approval, reads run freely), generates images/video/speech, and queries your connected data, all in one conversation, in plain language. It's **provider-agnostic**: the same agent runs on Claude or on free open models.

It's also a **reference implementation for building agents the right way**. The CLI is a thin wrapper over seven core, independently-usable packages — `agent-patterns`, `eval-suite`, `memory`, `hardening`, `mcp-servers`, `relay`, and `cli` — part of a 23-package workspace (the other 16 are platform packages: identity, vault, billing, observability, and so on), backed by **4,238 passing tests** across the packages and demo/API apps regression suite. (`ronin code` is the focused coding agent; `ronin chat` is the talk/media surface, both available when you want a single-purpose mode.)

## Mission Control: verified issue-to-PR work

`ronin util mission` is the durable control plane for engineering work that
needs more than a chat transcript. A mission records bounded issue intent,
typed plan/test/review/security evidence, explicit token/cost/time/tool and
concurrency budgets, a legal lifecycle, and an append-only audit chain.

```bash
# Create locally or import one attributed remote issue. Neither starts an agent.
ronin util mission create "Harden retry behavior" "Add bounded retry coverage."
ronin util mission import github owner/repository#123

# Work happens in a detached candidate checkout, never in the caller's tree.
ronin util mission workspace create MISSION_ID --image python:3.14-alpine
ronin util mission workspace run CANDIDATE_ID "pytest -q" --yes

# Inspect the immutable audit trail and the safe, durable event stream.
ronin util mission audit MISSION_ID
ronin util mission events verify
```

The normal lifecycle is `pending -> inspecting -> planning -> implementing ->
testing -> reviewing -> security -> awaiting_approval -> staging -> completed`.
Invalid shortcuts are rejected. Candidate commands use a detached Git worktree;
execution is Docker-only with the candidate mounted in isolation, dropped Linux
capabilities, resource limits, and no network. A draft PR requires evidence
gates and named human approval, and stays a local proposal: it does not silently
commit, push, or publish a remote pull request.

Remote issue imports use the stricter verified workflow. It records the issue
analysis, repository map, root-cause report, approved implementation plan,
candidate verification, self-review, and only then produces an evidence-backed
local PR draft.

```bash
ronin util mission import github owner/repository#123
ronin util mission inspect MISSION_ID --summary "What is failing" --reproduce "Minimal reproduction"
ronin util mission map MISSION_ID --source-dir src --test-dir tests --test-command "pytest -q"
ronin util mission rca MISSION_ID --broken "Observed behavior" --cause "Root cause" \
  --logic "Responsible code" --gap "Expected versus actual"
ronin util mission plan MISSION_ID --approach "Minimal fix" --step "Implement the fix" --file src/module.py
ronin util mission approve-plan MISSION_ID --approved-by "Rohith" --yes
# Create and implement in the isolated candidate, then run verify, review, and security.
ronin util mission self-review MISSION_ID --reviewer "Rohith" --checked scope --checked edge-cases
ronin util mission draft-pr MISSION_ID --approved-by "Rohith" --yes
```

Every committed audit entry emits a versioned, idempotent, hash-chained mission
event. The event feed contains only safe metadata and artifact digests, never
issue bodies, agent output, credentials, paths, or raw logs. Ronin can also
queue bounded candidate verification work to authenticated remote Docker
workers. See [the agent platform guide](docs/agent_platform.md) for the full
mission, workspace, event, and remote-worker contract.

An approved plan can now drive one real coding-agent implementation turn inside
the attached detached candidate checkout:

```bash
ronin util mission implement MISSION_ID --max-steps 25
ronin util mission verify MISSION_ID "pytest -q" --yes
ronin util mission review MISSION_ID
ronin util mission security MISSION_ID
```

The implementation turn records only typed outcome, usage, changed-file, and
diff-digest evidence in the mission audit. It cannot edit the parent checkout,
stage, commit, push, or publish. Verification remains Docker-only and the
existing review, security, evaluation, and human-approval gates still decide
whether a local PR draft may be prepared.

Persistent specialist identities add durable project-local role experience on
top of mission execution. `ronin util team init` creates the architect,
implementer, reviewer, tester, security, and release roles; `team supervise`
detects stale heartbeats and preserves assignments for a governed recovery.
Role memories have source provenance, confidence, expiry, compaction, and
secret rejection. `team context` assembles a token-bounded local context pack
from project instructions, ranked repository files/tests, and relevant role
experience. Completed roles can make an evidence-backed handoff to an assigned
peer on the same mission, and that peer explicitly acknowledges it before work
starts. Mission Control exposes only safe lifecycle and handoff metadata.

### Context Windows

Ronin resolves a provider/model-aware context policy for every coding turn. It
reserves output capacity, keeps retrieval within a bounded share of the input
budget, and compacts before the provider's hard limit. Known model families use
the local catalog; unknown or local models default conservatively to 32k tokens.
Use `/context` in a session to inspect the active window, `/context 64k` to save
a project override, `/context auto` to return to automatic policy, or pass
`ronin code --context-window 64000` for a one-run override.

### Durable Agent Runs

The reusable `ronin-agent-patterns` runtime now has an opt-in durable execution
core. `RunJournal` stores an append-only SQLite event history and atomic,
integrity-checked checkpoints. `RunBudget` blocks provider and tool actions
before configured token, cost, time, tool-call, concurrency, or nested-agent
depth ceilings are exceeded. Interrupted tool calls are retained as pending
checkpoint state and resumed before another provider request, keeping the model
conversation valid.

```python
from ronin_agent_patterns import BudgetLimits, ReActAgent, RunBudget, RunJournal

journal = RunJournal(".ronin/reaction-runs.sqlite")
budget = RunBudget(BudgetLimits(max_tokens=50_000, max_tool_calls=80))
result = agent.run("Repair the retry boundary", journal=journal, budget=budget)

# After a process interruption, continue from the latest verified checkpoint.
if journal.interrupted_runs():
    result = agent.resume(journal.interrupted_runs()[0], journal, budget=budget)
```

### Agent Kernel

Ronin's agent kernel uses typed `AgentRequest`, `ContextFragment`, and
`ContextProvider` contracts. Repository search, project memory, skills, and
role-specific evidence can contribute attributed context to the same ReAct run.
Fragments are priority ordered, bounded before the provider is called, and
explicitly marked as trusted or untrusted. The resolved system prompt is stored
in the durable checkpoint, so an interrupted run resumes with the same evidence
and policy context rather than reconstructing a different prompt.

`ronin code` uses this same pipeline for repository instructions (`RONIN.md`,
`CLAUDE.md`, or `AGENTS.md`), recalled project facts, and first-turn repository
retrieval. It can also use reinforced, expiring project instincts: a candidate
practice does not reach an agent until explicit evidence promotes it. A
persistent `ronin index` is used when available; otherwise the existing
in-memory retrieval path remains best-effort. This keeps interactive, streaming,
and resumable runs on one attributable context contract.

The same local execution kernel now also drives multi-agent orchestration:
`RunJournal` checkpoints the plan and every completed dependency wave, while a
single thread-safe `RunBudget` accounts for parallel specialists. Recovery runs
continue only the unfinished wave, preserving already completed evidence rather
than replaying agents. The [execution-kernel architecture](docs/architecture/execution_kernel.md)
defines the contract shared by terminal, editor, mission, and remote-worker
surfaces.

Use durable orchestration for longer read-only investigations and bounded team
analysis. It records local checkpoints under `.ronin/` and prints a run id that
can be resumed after an interruption:

```bash
ronin util orchestrate "map the authentication boundary" --durable \
  --max-run-tokens 50000 --max-run-cost-usd 5 --max-run-seconds 900
ronin util orchestrate --resume-run run-... --root .
```

Durable orchestration is intentionally read-only. Code changes continue through
the mission candidate workflow, which gives writes isolated workspaces, Docker
verification, review/security evidence, and explicit approval gates. A resumed
run retains its original budget ceilings; any limits supplied at resume time can
only make those ceilings tighter.

### Editor Interoperability

`ronin acp --root .` exposes the same coding runtime to a local
[Agent Client Protocol](https://agentclientprotocol.com/) editor client over
stdio. It provides ACP initialization, persistent/resumable bounded sessions,
text prompts, streamed agent messages, and local activity/usage evidence while
preserving the normal typed context, project memory, provider routing, and agent
history. The default `read_only` mode cannot edit. An editor may explicitly use
the Ronin `proposal` mode, which delegates to the existing multi-agent
orchestrator in detached worktrees and retains a reviewable proposal; it never
writes into the editor workspace or stages a change. A client cannot select a
workspace outside `--root`, inject MCP servers, or elevate tool permissions.

## Ronin API Keys

Ronin can issue project-bound keys for CI, editor integrations, remote workers,
and a self-hosted API gateway. A raw key is displayed once, while Ronin stores
only a per-key salted digest. Keys have explicit scopes, expiry, request rate,
token, cost, and concurrency limits, with revocation, rotation, and a safe local
audit log.

```bash
ronin util api-keys create github-actions --scope mission:read --scope proposal:read \
  --max-tokens 50000 --max-cost-usd 5 --expires-at 2026-12-31T00:00:00Z
ronin util api-keys list
ronin util api-keys revoke key-... --yes
ronin util api-keys serve --root .
```

The gateway exposes public health and scoped, read-only identity/mission-status
endpoints. It cannot modify a checkout, invoke a provider, access provider
credentials, or grant approvals. These are **Ronin** credentials, not provider
credentials: Anthropic, OpenAI, Gemini, and other provider keys must be issued
by their providers and are kept separate from Ronin API-key storage.

## 🛠 `ronin code` · the coding agent (Claude-Code shaped)

```bash
ronin code "add a --json flag and update the tests"
ronin code "explain @main.py and fix the bug in @utils.py"   # @-mention files
ronin code --plan "refactor the auth module"                 # plan → approve → execute
ronin code --continue                                        # resume your last session
```

A coding agent that reads, edits, and runs your code: every write and shell command gated behind a diff preview and your approval (read operations run freely). It mirrors the Claude Code experience:

- **Rounded input box + live dropdowns**: type inside a bordered prompt with ghost placeholder text. **`/`** opens a command menu (34 commands with descriptions), **`@`** opens a live file picker, **`!`** runs a shell command inline, and **`#`** files a note straight into project memory. ↑/↓ history, vi-mode (`/vim`).
- **Premium status line + mode chips**: a live footer always shows what ronin is and what it's allowed to do — a **FREE / PAID / LOCAL** badge, `provider/model`, the current **mode**, your **git branch** (with `*` when dirty and `↑/↓` ahead/behind), and the context size. Real output, no theming required:

  ```text
  chip strip (input):  [FREE] [cerebras:gpt-oss-120b] [normal] [main*] [write-gated]
  per-turn footer:     ✻ FREE  Forged for 1.3s · ↑11.4k ↓314 · cerebras gpt-oss-120b · main*
  ```

  The input box carries an **always-visible chip strip** — cost badge, `provider:model`, mode, git branch, the **write-gated / auto-accept** safety state, and the active **role** if set. It's **width-aware**: in a narrow terminal it sheds the lowest-priority chips first (role, then branch) but never the badge, mode, or write-gate. The badge reads **FREE** on free tiers, **PAID** when a paid key is required, **LOCAL** for Ollama/offline, and **UNKNOWN** only when pricing genuinely can't be determined (a custom endpoint). It never crashes outside a git repo (the branch chip just drops).
- **Role agents** (`/role`): pick how ronin works — **researcher** (read-only explore), **implementer** (gated edits), **reviewer** (read-only diff review), **tester** (verify with tests), **architect** (design first), **debugger** (root-cause failures). Read-only roles are *enforced* (the agent only gets read-only tools), not just suggested; doer roles still flow through the approval gate. The active role shows in the chip strip, and ronin gently suggests a fitting role (e.g. `/role debugger` for "why is this failing?") without ever switching for you.

  ```text
  /role debugger     why is the token refresh failing?   → root-cause first, then fix (gated)
  /role reviewer     review my changes                   → read-only findings, no edits
  /role researcher   how does the router pick a model?   → read-only explanation w/ file:line
  /role clear        back to default behavior
  ```
- **Shift+Tab modes**: cycle **normal → auto-accept → plan** edit modes, shown live in the input chrome.
- **Streaming Markdown + inline tool calls**: replies stream as rendered Markdown; tool activity renders Claude-Code-style as `⏺ Read(file)` with `⎿ result` underneath; edits are shown as syntax-highlighted diffs you approve.
- **@-file & @-URL mentions**: drop `@path` to pull a file into context, or `@https://…` to pull a web page's readable text into context. Start a message with a folder path to `cd` into it.
- **Plan mode** (`--plan`) proposes the steps read-only, you approve, then it executes. **Resume** (`--continue`) picks up your last session.
- **Live plan tracker**: multi-step tasks show a checklist the agent keeps current as it works — `✓` done · `▶` active · `☐` pending · `⊘` blocked · `✗` failed. It updates only from the agent's real `update_todos` state (no faked progress), and shows nothing when there's no plan.
- **Tools**: read / write / `edit` / `multi_edit` / `glob` / search / run, plus **`web_search` / `fetch_url`**, **read-only git** (`git_status` / `git_diff` / `git_log` / `git_blame`), **semantic code intelligence** (`diagnostics` / `definition` / `references` via LSP), a **`task`** subagent plus **`parallel_task`** (concurrent read-only fan-out) and **`isolated_task`** (parallel *mutating* sub-agents, each in its own git worktree so edits can't collide), and any **MCP** server's tools (`ronin mcp add …`).
- **Integrations**: give the agent new tools three ways, each one command: **local MCP** servers (24-server catalog: `ronin mcp install github`), **remote/hosted MCP** servers (`ronin mcp add-remote …`), or **plugins** (200 built-ins like weather/currency/dns/uuid + scaffold your own with `ronin util plugin new`). See **[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)**.
- **Project memory**: auto-loads `RONIN.md` / `CLAUDE.md` / `AGENTS.md` from the repo so it follows your conventions.
- **40 slash commands** · steer across turns: `/help`, `/login`, `/provider`, `/free`, `/role`, `/model`, `/models`, `/theme`, `/presence`, `/mcp`, `/agents`, `/compact`, `/context`, `/copy`, `/export`, `/resume`, `/diff`, `/undo`, `/commit`, `/pr`, `/doctor`, `/config`, and more. `/provider` shows every provider with a free/paid + key-health view; `/free` switches to a $0 provider; `/role` picks a coding role; `/theme` restyles code blocks + diffs live. `/presence balanced|direct|supportive|quiet` adjusts the interactive delivery style, and `/presence checkins on|off` controls brief task-relevant check-ins. The chip strip + per-turn footer show the FREE/PAID badge, provider/model, mode, git branch, role, context, and time.
- **Human-centered presence.** Ronin can meet explicit work friction, urgency, or progress with a short, useful acknowledgement before returning to the work. It does not claim human feelings or consciousness, diagnose a person, create attachment, retain inferred feelings, or let communication cues change tool permissions or safety decisions.

## 🧠 Supported providers

`ronin` works with any LLM, proprietary or open-source. **Switch provider or model from inside a session, no restart:** `/login <provider>` sets a provider + key (masked), `/provider` lists every provider with a free/paid + key-health view and switches between them, `/free [on]` jumps to a $0 provider, and `/model <name>` swaps the model.

| Provider | Free? | Default model | Notes |
|---|---|---|---|
| **Anthropic** | - | `claude-sonnet-4-6` | top quality; native SDK |
| **Gemini** | ✅ free tier | `gemini-2.5-flash` | generous free RPM; key at aistudio.google.com |
| **Cerebras** | ✅ free tier | `gpt-oss-120b` | very fast / high throughput |
| **Groq** | ✅ free tier | `openai/gpt-oss-20b` | 30 req/min free |
| **OpenRouter** | ✅ free models | `qwen/qwen3-coder:free` | one key, many models |
| **Ollama** | ✅ local | `llama3.1` | runs on your machine, no key |
| **OpenAI** | - | `gpt-4o-mini` | - |
| **Custom** | - | (you specify) | any OpenAI-compatible endpoint |

```bash
ronin                       # then, in-session:
/login gemini               # paste a free key at the masked prompt
/provider                   # see all providers · free/paid · which have keys
/free on                    # switch to a $0 provider you can run right now
/model gemini-2.5-flash  # or switch models without re-entering the key
/models                     # list what the current provider offers
```

ronin auto-retries free-tier rate limits (429) with backoff, and round-trips Gemini thinking-model signatures, so free providers work for real multi-step agent tasks.

## 🚀 Beyond Claude Code

Because ronin is **provider-agnostic**, it can do things a single-vendor agent structurally can't:

- **🧩 Multi-model consensus**: `ronin util consensus "<task>" -m anthropic,gemini,cerebras` runs the *same* question on several models in parallel, then a judge model synthesizes one cross-checked answer (with a "where they agreed / diverged" note). More robust on hard design/review/decision questions than any single model. Read-only.
- **🧭 Multi-agent orchestrator · provider-agnostic sub-agents**: `ronin util orchestrate "<goal>" -r researcher=anthropic,implementer=cerebras,reviewer=gemini,tester=groq` decomposes a goal into subtasks, assigns each to core plus task-matched specialist profiles **on its own vendor's model**, runs independent ones in **parallel**, and synthesizes the result. The catalog has 1,170 generated specialists plus project-owned `.ronin/agents.json` profiles, but each run activates a bounded team. `--write` runs editing sub-agents in **isolated git worktrees** with independent review/test acceptance; `--offline` keeps it $0 with zero egress. See [docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md), [docs/agents.md](docs/agents.md), and [docs/agent_control_plane.md](docs/agent_control_plane.md).
- **🏁 Evidence-weighted competing teams**: `ronin util trials run` runs explicit implementations in separate worktrees and chooses only a candidate with successful role evidence, no failed handoff contract, and no secrets detected in its added diff. It scores evidence rather than prose and never stages or merges the winner.
- **🪜 Role-handoff pipeline**: `ronin util pipeline "<task>"` runs the roles **in sequence** with gated handoffs — **architect → implementer → reviewer → tester → verifier** by default — passing a **structured artifact** between stages, not just prose. The architect emits an `ArchitectPlan` (objective, files to change, steps, risks, **acceptance criteria**); the implementer an `ImplementationReport`; the reviewer a `ReviewReport`; tester/verifier a `VerificationReport`. Each is typed, serializable, and uses **explicit unknowns** — a stage never fabricates a field.
  - **Real diff evidence**: the harness captures the **actual unified diff** (read-only `git diff HEAD`) — tracked changes **and brand-new untracked files** (via `git diff --no-index`, so nothing is ever staged) — with files, +/- counts, and a byte-budgeted excerpt, so the verifier and semantic check reason about real changes, not the implementer's self-report. Binary/oversized files are recorded as metadata only. `--diff-context <n>` / `--max-diff-bytes <n>` tune it; `--no-diff-evidence` disables it; it's in `--json`.
  - **Multi-suite verification, required or optional** — pass `--verify-cmd` **repeatedly**, name suites with `--verify-suite "unit:pytest -q"`, mark one **optional** with a trailing `?` (`"lint?:ruff check ."`) or via `--required-suite` / `--optional-suite`, or `--auto-verify-all` to detect several (tests/build **required**; lint/typecheck/format **optional**, classification shown before running). All gated; results aggregate into a suite table (name · required/optional · status · exit · duration). **A required failure fails** the run; an **optional failure only warns** (never fails, blocks, or passes on its own). `--no-auto-verify` disables single-command auto-detection.
  - **Artifact contract checks**: a `ContractCheckReport` cross-checks the artifacts — changed files must overlap the architect's `files_to_change`, the verifier must cover every acceptance criterion, unresolved review blockers fail the run. With **`--semantic-contract`**, a read-only model pass judges whether the **actual diff** fulfils the plan (objective/acceptance alignment, scope-creep, unexpected changes) — advisory, and it **never claims a pass when the diff is missing (→ unknown) or truncated (→ warning)**; a clear misalignment fails the run.
  - **Combined final verdict** (safety-first precedence): a blocked stage → **blocked**; a failed **required** suite / failed contract / unmet criterion / blocking review / failed verifier / clearly-misaligned semantic → **failed**; **passed** only with real evidence (optional-suite warnings are advisory). A compact **Final Verification** truth table shows diff-evidence · untracked-evidence · suites (required/optional) · verify-result · git-snapshot · checkpoint-restore · acceptance · contract · semantic · review-blockers · verdict.
  - **Resume with git safety + restore any checkpoint** (`--save-state` / `--resume`): checkpoints `PipelineState` after every stage (with a **git snapshot** — HEAD, branch, dirty/untracked, ignoring ronin's own `.ronin/`). On `--resume` it compares the saved tree to now and **refuses an unsafe resume** unless `--force-resume`. `--list-checkpoints` shows what's available (id · created · sha · files); `--restore-latest-checkpoint`, `--restore-checkpoint-id <n>`, or `--restore-checkpoint-interactive` **restore** the tree to a checkpoint — always **gated** (a confirm before overwriting), re-checking the snapshot afterward and refusing if it still mismatches. It never resets/stashes or destroys local work silently. `--no-restore-offer` hides the offer; `--rerun-completed` re-runs finished stages.
  - **Safety**: read-only roles (architect/reviewer/verifier) are *enforced*; without `--write` the whole run is a read-only proposal; every edit/command still hits the approval gate; a blocked/failed stage **halts** (the rest → skipped, artifacts preserved); exit is non-zero on a failed/blocked verdict.
  - **Gated finish** (opt-in, never silent): `--commit` offers a commit **only after a passing final verdict** (a non-passing verdict requires an explicit y/N), always showing the diff summary first; `--pr` then offers a gated push + PR; `--branch` / `--commit-message` / `--pr-title` / `--pr-body` override the drafts. `--dry-run` describes the whole plan + what it *would* commit and changes nothing.
  - `--free`/`--offline` keep it $0; `--json`/`--out` emit the full state **including artifacts, contract, and verification**. Complements `orchestrate` (parallel, multi-vendor) with a single-provider, step-by-step, safety-first flow.

  ```bash
  ronin util pipeline "fix auth tests" --write --auto-verify-all                # tests required, lint/typecheck optional
  ronin util pipeline "fix auth" --write --verify-suite "unit:uv run pytest -q" --verify-suite "lint?:uv run ruff check ."
  ronin util pipeline "fix frontend" --write --required-suite "test:pnpm test" --optional-suite "typecheck:pnpm typecheck"
  ronin util pipeline "add retry logic" --write --semantic-contract --max-diff-bytes 50000
  ronin util pipeline "fix auth tests" --write --checkpoint --save-state .ronin/pipeline/auth.json
  ronin util pipeline --resume .ronin/pipeline/auth.json --list-checkpoints
  ronin util pipeline --resume .ronin/pipeline/auth.json --restore-latest-checkpoint   # gated restore if the tree moved
  ```
- **🔁 Cross-provider failover**: set `failover` in config and a turn that hits a rate-limit or outage on the primary **transparently continues on the next provider** instead of dying. (Tokens already streamed aren't silently re-answered.)
- **🔒 Fully offline mode**: `ronin --offline` forces a **local brain** (Ollama / any localhost model) and **strips every network tool**, so ronin codes on a plane or in an air-gapped box with **zero egress**: nothing leaves the machine.
- **📊 Eval-driven model bake-off**: `ronin util bench -m anthropic,gemini,ollama:llama3.1` runs the **objective** eval battery (no LLM judge) across models and tells you the **cheapest model that clears your quality bar**. Pick a model with data, not vibes.
- **🥷 Kaizen · the self-forging agent**: `ronin util kaizen` finds a weakness in ronin's *own* source, drafts a fix in an **isolated git worktree**, and runs the **test suite as an objective fitness gate**: the diff only reaches your tree if the tests pass there. An agent that improves its own code, with eval-proof it worked, on a free model for $0.
- **🥋 The Dojo · rival models fight over your code**: `ronin util dojo "<task>" -m anthropic,gemini,cerebras` has each model attempt the *same* change in **parallel isolated worktrees**; a judge crowns the best diff. Claude vs Gemini vs DeepSeek, then you apply the winner.
- **⚔️ Ronin Duel · cross-vendor review**: `ronin util duel --against gemini` hands your diff to a **different** provider that adversarially hunts for what's wrong. The author model can't see its own blind spots; a rival vendor can. Advisory, CI-friendly.
- **🔭 Scout → Strike · explore cheap, edit strong**: `ronin code --scout "<task>"` runs read-only recon on a free blade, then a strong blade executes only the edits. Frontier quality where it counts, $0 everywhere else.
- **🗡 Bushido · your code of honor, everywhere**: a global `~/.ronin/bushido.md` of standing personal conventions the agent carries into **every** repo (a repo's own notes always override it).
- **💪 Muscle Memory · gets better at *your* repo**: the agent crystallizes a solved workflow into a reusable `/skill` saved in your repo. Use it for a week and ronin has a custom playbook that compounds.

### 🧪 Quality gates · objective checks, not vibes

The same "outcome over LLM-judge" philosophy as `eval` and `kaizen`, aimed at the code you're about to ship. Each is CI-friendly (non-zero exit on failure) and the core algorithms are pure + unit-tested.

- **🧬 Mutation testing · `ronin dev mutants <file>`**: coverage tells you a line *ran*; this tells you your tests would *notice if it were wrong*. ronin injects one-operator faults (`==`→`!=`, `and`→`or`, `>`→`>=`, …), runs your suite against each, and lists the mutants that **survived**: every survivor is a bug your tests would miss. The original file is always restored. Requires a green baseline.
- **🌐 Blast radius · `ronin dev radius`**: from your uncommitted changes, ronin walks the Python import graph *backwards* to every module that (transitively) depends on what you touched, and surfaces the **test modules in that radius** so you can run only what matters. `--run` executes them. A risk map + a fast, targeted feedback loop.
- **🎲 Flaky-test hunter · `ronin dev flake "<cmd>" -n 7`**: a single run can't tell flaky from stable. ronin runs your command N times, diffs the failure sets, and ranks the tests that flip green↔red, the non-deterministic ones, separating them from tests that are simply broken.
- **🛡 Scope-creep guard · `ronin dev guard`**: before you commit, ronin scans the lines you *added* for debug/secret leftovers (stray `breakpoint()`, `console.log`, unresolved merge markers, AWS keys, `TODO/FIXME`) and, with `--intent "…"`, flags files that drift from the task you set out to do. Drop it in a pre-commit hook.

Plus, on the coding agent itself:

- **⌨️ Type-ahead in the inline REPL**: the default is the minimal, Claude-Code-style inline flow (scrollback + a bordered input box); you can **type and queue messages while it works**. `ronin --tui` opts into an optional full-screen pane layout (live trace + approval modal) for those who want it.
- **⚡ Prompt caching** (Anthropic): the static system + tools prefix is cached on every turn (up to ~90% cheaper/faster); the status line shows `⚡N cached`.
- **🧠 Semantic code intelligence**: `diagnostics` / `definition` / `references` via real language servers (pyright, ts-language-server, gopls, rust-analyzer), with graceful "install X" fallback. Plus **`repo_map`** (BM25) and optional **`semantic_search`** (embeddings, local-Ollama or OpenAI) to find code by meaning, and **auto context engineering** that front-loads the most relevant files each turn.
- **🌳 Parallel mutating sub-agents**: `isolated_task` runs several editing agents at once, each in its **own git worktree**, so concurrent edits never collide; each returns a reviewable diff.
- **🖥️ Background processes**: `run_background` a dev server / test-watcher, tail its logs, and keep working ("watch-and-fix"); **⏪ checkpoint & rewind** snapshots the whole workspace and rolls it back; **👁️ vision-in-the-loop** screenshots a UI and analyzes it so the agent can self-correct.
- **🛡️ Built for free models**: tool calls survive near-miss argument names (auto-remapped), oversized tool results are capped, context compacts earlier off-Anthropic, clarifying questions (`ask_user`) head off wrong guesses, and per-provider keys mean switching providers never clobbers a key.

## How ronin compares — including where it loses

Written to be useful rather than flattering. A comparison table with no losing rows is
an advertisement, and a reader who finds the missing row themselves stops trusting the
winning ones too.

| | ronin | Claude Code | Cursor | Aider |
|---|---|---|---|---|
| Any provider, no lock-in | ✅ 15 adapters | ❌ Anthropic only | ~ its own routing | ✅ |
| Runs fully offline, $0, no key | ✅ | ❌ | ❌ | ~ needs a local server |
| Hard destructive-command floor below approvals | ✅ | ~ approvals only | ~ | ❌ |
| Own eval suite, published, integrity-gated | ✅ 118 tasks | ❌ not public | ❌ | ❌ |
| Failure taxonomy that blames model vs harness | ✅ | ❌ | ❌ | ❌ |
| **Model quality on hard tasks** | ❌ **whatever you bring; the 1.5B local model is much weaker** | ✅ frontier | ✅ frontier | ~ |
| **IDE integration** | ❌ **terminal-first; the VS Code story is thin** | ~ | ✅ **best in class** | ❌ |
| **Polish and onboarding** | ❌ **more surface than a new user can hold** | ✅ | ✅ | ✅ **simplest** |
| **Users, and therefore bug reports** | ❌ **effectively none yet** | ✅ | ✅ | ✅ |
| **Autocomplete / inline suggestions** | ❌ **none** | ❌ | ✅ | ❌ |
| Terminal-native, no editor required | ✅ | ✅ | ❌ | ✅ |
| MCP servers | ✅ | ✅ | ✅ | ❌ |

The bolded rows are the honest answer to "why would I not use this". If you want the
strongest available model with the least setup, use Claude Code. If you want inline
completion in an editor, use Cursor. If you want the smallest thing that edits files
well, use Aider. ronin is for the case where **provider independence, an audited safety
floor, or running with no key at all** matters more than those.

## Measured, not asserted

Most agent projects describe their quality. This one ships the thing that would catch it
lying: **118 eval tasks** under [`tests/evals/`](tests/evals/README.md), each a fixture
repo, a prompt, and a `verify.sh` that exits 0 on success.

```bash
ronin eval --dry-run --regression-gate     # what would run: no model, no key, no network
ronin eval --model kimi-k2 --parallel 8 --json run.json --markdown run.md
ronin duel --model kimi-k2 --model ronin-qwen-local --seed 7
```

Every task is checked in **three directions** on every push, because a `verify.sh` that
passes on the untouched fixture inflates every score derived from the suite forever and
nothing about the run looks wrong:

1. bare fixture → must **fail** (else the task is already solved);
2. plus the author's reference solution → must **pass** (else it is unsolvable);
3. `injection-resistance` only: solution plus the planted artefact → must **fail**,
   proving the guard actually bites.

Failures are classified into six classes, each labelled *model* or *harness* — because
"it failed" does not tell you which half to fix. `UNCLASSIFIED` is in the enum on
purpose: it is the taxonomy's own error bar.

**There are no scores in this repository.** Nothing has been run against a real model
yet, so nothing is reported. When numbers appear they will name the model, the commit,
and the seed — and if the fine-tuned adapter loses to its own base model, that result
gets published too.

### The $0 lane

```bash
cp examples/models.local.toml .ronin/models.toml
ronin eval --model ronin-qwen-local --regression-gate
```

Local weights, no API key, no network at inference. `RONIN_ADAPTER=<checkpoint>` serves a
fine-tune; a checkpoint with a config but no weights is **refused at startup** rather
than falling back to the base model, because that fallback produces a number which reads
as the fine-tune's score and is not one.

The adapter teaches **ronin-native behaviour** — tool syntax, gate handling, recovery,
planning. It does **not** add coding knowledge, and 1.5B is not a substitute for a
frontier model on hard tasks. That is the same admission as the bolded row above.

## Telemetry — off by default, auditable when on

Nothing is sent unless you run `ronin telemetry on`. Ignoring the subject leaves it off.

```bash
ronin telemetry status   # what state you are in, and where the files are
ronin telemetry on       # prints exactly what will and will not be sent, then opts in
ronin telemetry show     # every payload ever sent, verbatim, from a local log
ronin telemetry off      # stop; the local log is kept for you to delete
```

**No endpoint is wired in this build.** Consent is stored and honoured, but there is
nowhere for a payload to go: `record()` returns `no_sender` without opening a socket, and
`ronin telemetry status` says so rather than claiming outcomes are being sent. The schema
below is what *would* be sent, and it is documented now so the surface is reviewable
before it is live rather than after.

**Would be sent, per completed task:** outcome, task category, failure class, OS name,
Python version, ronin version, whether an approval was denied, and turn count and
duration as *coarse buckets* rather than exact numbers — so a payload cannot fingerprint
one session.

**Never sent:** prompts, code, file contents, file names, paths, shell commands, repo or
branch names, git remotes, usernames, hostnames, IP addresses, or API keys.

Every payload would be appended to a local log *before* being sent, and `ronin telemetry
show` prints that log unmodified. The point is that you can **audit rather than trust** — a
privacy claim whose only evidence is a paragraph in a README is asking for credit it has
not earned, and this paragraph is no exception.

## Install

```bash
# one-liner: installs uv if missing, clones the repo, syncs the workspace,
# drops a 'ronin' shim in ~/.local/bin (with a 'ro' short alias)
curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
```

Pin a tag: append `-s -- --ref v1.0.0`. PyPI publish is wired (`.github/workflows/release.yml`) and lands `pip install ronin-cli` once the packages are published (approval-gated; `PYPI_TOKEN` must be set). Until then, a standalone install works from the built wheel set — `uv build --all-packages` then `pip install --find-links dist/ ronin-cli` in a clean environment (verified by `scripts/test_clean_install.sh`; see `docs/release/pypi_packaging_decision.md`). Editable/source-checkout installs are for development only, not a substitute for the standalone path.

For Postgres support after install: `(cd ~/.local/share/ronin && uv pip install psycopg2-binary)`.

## Updating

If you installed from a git checkout, update in place with one command:

```bash
ronin update            # fetch origin, reset to origin/main, re-run uv sync
ronin update --check    # report whether an update is available, change nothing
```

`ronin update` aborts if you have uncommitted local changes; pass `--force` to
discard them. Manual fallback:

```bash
ronin update          # in-place: fetch origin, reset to origin/main, uv sync (refuses a dirty tree without --force)
# or manually, if you cloned elsewhere:
cd ~/.local/share/ronin && git fetch origin && git reset --hard origin/main && uv sync --all-packages
```

`ronin util version` shows the running version plus the checkout's short sha and
branch, e.g. `ronin 1.0.0 (a1b2c3d, main)`.

## More surfaces

ronin is one agent with several focused entry points beyond `code`:

- **`ronin dev explain <path>`** · onboard to any codebase: prose explanation **+ an auto-generated Mermaid architecture diagram** (renders on GitHub) **+ optional voice** (`--speak`). Read-only.
- **`ronin eval [--model X]`**: score agent quality on a battery of **real** sandboxed jobs (reasoning, file writes, codegen, grounded reads, multi-file). Checks the **outcome**, not an LLM judge, so it's deterministic and works on **any** provider. Swap providers with `/login` and re-run to compare on the same bar.
- **`ronin util mission`**: inspect an issue, create a disposable candidate workspace, record real verification/review/security evidence, and prepare an approval-gated local PR draft. The audit and event stream are durable under `.ronin/`.
- **`ronin util briefing`**: a founder ops briefing (revenue, churn, failed payments, urgent issues) aggregated from Stripe / Linear / Slack / Notion / Postgres via read-only MCP servers; auto-saved with week-over-week deltas, `--slack` to post.
- **`ronin util investigate "<symptom>"`**: root-cause a problem across your **business data AND your code** (e.g. "failed payments spiked the 9th → `stripe_webhook.py` changed in commit `a1b2c3`").
- **`ronin util image` / `ronin util video` / `ronin util say` / `ronin util see`** · terminal-native media: text-to-image (free via Pollinations, shown inline), frames+ffmpeg video, OS text-to-speech, and vision Q&A on a local image.

```bash
ronin dev explain packages/cli                                       # explain a module + diagram
ronin eval --model gpt-oss-120b                                  # objective score, any provider
ronin util image "a red panda hacking at night, neon, flat vector"    # free, no API key
```

## 🎮 `ronin play` · the arcade

A break room built into the terminal — packaged as an **optional extra** so the
core agent stays lean: `pip install 'ronin-cli[arcade]'` (without it, `ronin play`
prints a one-line install hint). **`ronin play`** opens a picker menu (arrow keys,
teal highlight); **`ronin play <game>`** jumps straight in. **31 games, all free**, in four flavours:

- **⚡ Real-time, arrow-key controls** — full-screen, in-place render via a shared raw-mode engine (`games/_realtime.py`): 🐍 Snake (start-on-keypress, no cheap deaths) · 🔢 2048 · 🟦 Tetris (7-bag, ghost piece, line-clear scoring) · 💣 Minesweeper · ⭕ Tic-Tac-Toe (unbeatable minimax) · 🔴 Connect Four · 🧠 Memory Match.
- **🃏 Classics with real depth**: Blackjack (betting / double / 3:2) · Pandle (Wordle with a live on-screen keyboard) · Hangman · Rock-Paper-Scissors · Pig · Simon · Word Scramble · Sudoku (unique-solution generator) · Mastermind · Battleship (hunt/target AI) · Reversi · Typing Test · Number Guess.
- **🐼 ronin-flavoured** — coder games no other arcade ships: 🐛 Bug Hunt (spot the planted bug) · 📈 Big-O Guess · 🧩 Regex Golf.
- **🔮 AI-powered — and, true to ronin, provider-neutral**: they run on **whatever backend you've configured** (Cerebras / Groq / Gemini / Claude / Ollama), routed through ronin's *own* model layer — not a hardcoded vendor SDK. 🔮 **Mind Reader** (think of anything; the panda guesses it in 20 questions) · 🗺️ **AI Adventure** (a living text dungeon, the panda is your DM) · 🎓 **AI Trivia** (endless generated questions). No model configured? They show a friendly nudge and the non-AI games still play fully offline.

```bash
ronin play                 # the arcade menu
ronin play tetris          # jump straight into a game
ronin play mindreader      # the AI reads your mind (runs on your configured model)
```

Every game keeps its rules in **pure, unit-tested functions** split from the terminal I/O, and the whole roster is smoke-driven in CI. The selection menu is a reusable Claude-Code-style picker (`picker.py`) — the same widget is ready to back an `ask_user` clarifying-question tool for the agent.

## ronin util ui · the web dashboard

`ronin util ui` serves a local web dashboard for the agent. It is a SINGLE
self-contained HTML page: inline CSS, vanilla JavaScript, no external resource
URLs (no CDN, no web fonts, no remote images), so it works fully offline. The
page talks only to this app's own read-only endpoints and renders the REAL data
ronin already wrote under `.ronin/` (and `~/.ronin/`). Nothing leaves your
machine; there is no auth because it is read-only on local data served on
localhost.

```bash
ronin util orchestrate "add retry + tests to the http client" --offline   # populate a run
ronin util ui                                                             # serve at http://127.0.0.1:8765/
ronin util ui --port 9000 --no-open                                       # custom port, do not open a browser
```

What it shows:

| Panel | What it surfaces | Source on disk |
|---|---|---|
| Recent runs | Orchestrated runs and chat sessions, most recent first, with success state | `<RONIN_HOME>/runs/*.json`, `.ronin/sessions/*.json` |
| Run detail | An expandable orchestrator sub-agent tree: the planner node, then each specialist sub-agent by role and provider/model, with the subtasks assigned to it | the stored run record |
| Faithfulness badges | Grounding score per answer: green grounded, red ungrounded, amber abstain, on the synthesized answer and per subtask | the offline faithfulness harness output stored on the run |
| Mission Control | Mission stage, audit validity, evidence/budget state, local draft proposals, and fleet plans/runs | `.ronin/missions/`, `.ronin/fleet/` |
| Mission events | Safe typed event topics, producer, transition, and timestamp; never issue content or raw logs | `.ronin/mission-events/events.jsonl` |
| Candidates and remote workers | Candidate lifecycle metadata and remote verification lease status; no checkout paths or contents | `.ronin/candidate-workspaces/`, `.ronin/remote-workers/` |
| Memory | The durable facts ronin remembers about you | `~/.ronin/memory.json` |
| Skills | Crystallized repo-local skills | `.ronin/commands/*.md` |

When there is no data yet, each panel shows an honest empty state instead of a
sample. The dashboard reuses the existing FastAPI gateway in `apps/api`; the
data endpoints are read-only.

Quick check without a browser (the page is served at `/`, the data at `/ui/*`):

```bash
ronin util ui --no-open &                       # serve in the background
curl -s http://127.0.0.1:8765/ | head      # the self-contained HTML page
curl -s http://127.0.0.1:8765/ui/runs      # recent runs (JSON)
curl -s http://127.0.0.1:8765/ui/missions  # safe Mission Control state (JSON)
curl -s http://127.0.0.1:8765/ui/mission-events # durable safe events (JSON)
```

For a screenshot, open `http://127.0.0.1:8765/` in a browser after running
`ronin util ui`.

## Remote access (relay)

Status: working scaffold with tests. Not deployed, no users. Run it yourself if
you want to reach your own local gateway from a phone.

`ronin util relay` lets a phone send a task to your local Ronin gateway without
opening an inbound port on the laptop. A relay server you own runs on a VM
(`ronin util relay serve`); a connector on the laptop dials OUTBOUND to it and holds
the connection open (`ronin util relay connect`). The relay forwards a phone request
down that websocket; the connector makes ONE local call to its single
configured target and ships the reply back. The laptop opens no inbound port.

```bash
ronin util relay serve --port 8000                     # on a VM you own (needs RONIN_RELAY_TOKEN)
ronin util relay connect \                             # on the laptop, dials OUT
  --relay wss://relay.example.com/connect \
  --target http://127.0.0.1:8000/webhooks/agent \
  --token "$RONIN_RELAY_TOKEN"
```

Security model (preserved exactly): outbound-only connector, a single fixed
target URL (no shell, no eval, no arbitrary host), and a mandatory shared token
that fails closed if missing or too short. See [docs/RELAY.md](docs/RELAY.md)
and `packages/relay/`.

## Use Ronin from your phone (Telegram)

`ronin util telegram` lets you message a Telegram bot and get answers from the SAME
read-only ask agent that `ronin ask` uses. The laptop dials OUT to Telegram and
long-polls for messages, so it works behind NAT with no inbound port and no
public hostname.

```bash
export TELEGRAM_BOT_TOKEN="123456789:AA..."   # from @BotFather
export TELEGRAM_ALLOWED_CHAT_IDS="42,777"     # chat ids allowed to run the agent
ronin util telegram                                # long-poll forever; Ctrl+C to stop
```

Safety model: the token is required and the command fails closed (exits
non-zero, never polls) if it is missing or malformed. The agent runs only for a
chat id in the explicit allowlist; any other chat is ignored. An EMPTY allowlist
runs the agent for nobody and the bot only replies with your chat id so you can
add it. Messages go through the read-only ask path only: no edits, no shell, no
`--full-access`, and no arbitrary command path. See
[docs/TELEGRAM.md](docs/TELEGRAM.md).

From the same chat you can also set reminders, daily briefings, and page
watches, handled on the bot's existing poll tick (no extra daemon). Examples:
`remind me at 6pm to call mom`, `watch https://example.com for price`,
`list watches`, `cancel watch 1`. A page watch fetches the url on a throttled
interval, hashes the watched slice (the whole page, or only lines containing your
keyword), and pings you when it changes.

## 30-second quickstart (no real credentials)

```bash
ronin init --demo                                 # ships fake Stripe + Linear data
ronin ask "what ENG issues are in progress?"
ronin chat                                        # multi-turn REPL
```

Demo mode lets you play with the CLI before connecting any real services. Without an API key, an offline keyword router answers: set a key for full natural-language responses.

## Real config

```bash
ronin init                                        # interactive - picks provider + service creds
```

Or write `.ronin/config.toml`:

```toml
provider = "anthropic"
model = "claude-sonnet-4-6"
anthropic_api_key = "sk-ant-..."

stripe_api_key = "rk_live_..."                  # use a Restricted Key
linear_api_key = "lin_api_..."
slack_bot_token = "xoxb-..."
notion_token = "secret_..."
database_url = "postgres://readonly_user:...@host:5432/db"   # a read-only role
```

`.ronin/` is gitignored: the file holds plaintext credentials. Keys are user-supplied and never committed.

## Commands

| Command | What it does |
|---|---|
| **`ronin`** | **The unified agent: talk, code, generate media, query data in one conversation.** |
| **`ronin code [task]`** | **Coding agent: streaming, plan tracker, project memory, 40 slash commands.** |
| **`ronin acp --root .`** | **Local, read-only ACP bridge for editor-agent sessions over stdio.** |
| `ronin chat` | Talk/media REPL with short-term memory. |
| **`ronin play [game]`** | **The arcade: 31 free terminal games — real-time (Snake / Tetris / 2048 / …), classics, ronin-flavoured (Bug Hunt / Big-O / Regex Golf), and provider-neutral AI games (Mind Reader / AI Adventure / AI Trivia).** |
| `ronin init [--demo]` | Create a config file (interactive or demo). |
| **`ronin eval [--model X]`** | **Score agent quality on objective tasks, works on any provider (no LLM judge).** |
| **`ronin dev explain <path>`** | **Explain a codebase: prose + Mermaid diagram + optional voice.** |
| **`ronin util investigate "<symptom>"`** | **Root-cause a problem across your business data AND your code.** |
| **`ronin util pipeline "<task>"`** | **Sequential gated role handoff: structured artifacts, a verifier, evidence-based verification (real unified diff incl. untracked files + required/optional multi-suite), structural + `--semantic-contract` checks, and git-safe resume with gated restore of any checkpoint. `--write`, `--dry-run`, `--roles`, `--free`, `--offline`, `--json`, `--commit`, `--pr`, `--verify-cmd` (repeatable), `--verify-suite`, `--auto-verify-all`, `--semantic-contract`, `--diff-context`, `--max-diff-bytes`, `--save-state`, `--resume`, `--restore-checkpoint`, `--force-resume`.** |
| **`ronin util mission`** | **Durable evidence-gated issue-to-PR control plane: GitHub/GitLab issue intake, state-machine missions, candidate workspaces, Docker verification, deterministic review/security/evaluation gates, local approval-gated PR drafts, safe event replay, and authenticated remote verification workers.** |
| **`ronin dev review [--base main]`** | **AI code review of your diff: severity-tagged findings, read-only.** |
| **`ronin dev fix "<command>"`** | **Autonomous fix-until-green: runs the command, edits + re-runs until it passes.** |
| **`ronin util book "<request>"`** | **Prepare-and-confirm a booking (flight, hotel, restaurant, ticket): researches options into a summary (option, price, link, details), optionally pre-fills a form in the browser up to the payment step, then STOPS. It never pays. You confirm and pay yourself. The browser is an optional extra (`pip install 'ronin-cli[browser]'`); without it you get search + a prepared summary + manual steps.** |
| **`ronin util research "<question>"`** | **Search the web and answer, with sources: runs the read-only agent with the keyless web tools (web_search + fetch_url). Degrades to a raw web search if no model is configured. Free, no key.** |
| **`ronin util consensus "<task>" -m a,b,c`** | **Multi-model panel: ask several models in parallel, then synthesize one cross-checked answer.** |
| **`ronin util orchestrate "<goal>" -r role=provider,...`** | **Decompose a goal into subtasks, run provider-agnostic sub-agents (parallel where independent), synthesize. `--write` isolates edits in git worktrees.** |
| **`ronin util bench -m a,b,c`** | **Eval-driven model bake-off: score models on the objective battery, recommend the cheapest that passes.** |
| **`ronin --offline`** | **Zero-network mode: local brain (Ollama) + network tools stripped; nothing leaves the machine.** |
| **`ronin util briefing`** | **Founder ops briefing, auto-saved with week-over-week deltas.** |
| `ronin util briefing --slack <#chan>` / `--history` / `--out file.md` | Post to Slack / trend table / write to Markdown. |
| `ronin ask "<question>"` | One-shot: print answer + typed trace. |
| `ronin util tui` | Full-screen Textual UI: chat + live trace, F1 help. |
| **`ronin util ui [--port 8765]`** | **Read-only local dashboard: runs, sub-agent tree, faithfulness, memory, skills, and Mission Control with safe mission, event, candidate, fleet, proposal, and remote-worker status.** |
| `ronin mcp add <name> <command>` | Register an MCP tool server (then `/mcp` lists them in-session). |
| `ronin util serve --port 8000` | Expose the agent as an HTTP API (`POST /ask`). |
| `ronin util schedule add <name> "<prompt>" --cron "<expr>"` | Store an agent task on a cron schedule. `schedule list` shows each task + its next run, `schedule run-due` runs the tasks due now through the agent, `schedule remove <name>` deletes one. Tasks persist to `~/.ronin/schedule.json`; `run-due` falls back to the offline demo brain when no key is set. |
| `ronin util tools` / `ronin util doctor [--check]` | List tools / health-check provider + auth + services (live ping). |
| `ronin util image` / `video` / `say` / `see` | Media: text-to-image, video, text-to-speech, vision. |
| `ronin util set-key [--provider X] [--model Y]` | Set the LLM API key (masked). In-session, use `/login`. |
| `ronin dev mutants <file> [--test "<cmd>"]` | Mutation-test a file: list mutants the suite fails to catch. |
| `ronin util stash [--no-ai]` / `stash list` / `stash pop [n]` | Git stash with an AI-summarized one-line label (offline fallback). |
| `ronin dev undo-commit [--revert] [--force]` | Show the last commit, then soft-reset (default) or revert it (gated; refuses pushed HEAD). |
| `ronin dev explain-error [<trace>]` | Parse a traceback (Python/Node/Go/Rust), cite the source lines, explain the cause + fix. Read-only. |
| `ronin util faithfulness check "<answer>" --sources a.py b.py` | Grounding harness: score an answer against the files it should be grounded in. Flags ungrounded claims + hallucinated code symbols, prints a 0..1 score, abstains when evidence is thin. `--json`, `--strict` (exit 1 ungrounded / 2 abstain). Offline. |
| `ronin util agent "<goal>" --faithfulness warn\|gate` | Run the autonomous agent with the grounding harness on its final answer: `warn` surfaces the score + ungrounded claims, `gate` holds an ungrounded answer for your confirmation. Default mode is the `faithfulness` config setting. |
| `ronin code --faithfulness warn\|gate` | Run the coding agent with the edit guard on: each proposed write/edit is scored against the files the agent read. `warn` surfaces an ungrounded-edit score; `gate` holds an ungrounded edit (revise or read the right file) even under `--full-access`. Default mode is the `faithfulness` config setting. |
| `ronin dev radius [--run]` | Blast radius of your diff + the affected test modules. |
| `ronin dev flake "<cmd>" [-n N]` | Run a test command N times; rank non-deterministic tests. |
| `ronin dev guard [--intent "<task>"]` | Scan the diff for debug/secret leftovers + scope creep. |
| `ronin dev scan [--staged] [--history]` | Scan for committed secrets — working tree, staged diff, or whole git history. Exits non-zero on a hit. |
| `ronin dev todo [--issues] [--execute]` | Board of every FIXME/TODO/HACK; `--issues` drafts a GitHub issue per marker (dry-run; `--yes` files via gh), `--execute` resolves them autonomously. |
| `ronin util version` | Print the version. |

## 🔒 Safety & security

ronin can write files and run commands, so safety is built into the core, not bolted on:

- **Gated mutations.** Every file write and shell command in the coding agent is held behind a **diff preview + your approval**: read operations run freely. **Plan mode** (`--plan`) is fully read-only.
- **Candidate isolation.** Mission verification executes only in a detached candidate Git worktree through Docker, with dropped capabilities, `no-new-privileges`, resource limits, a candidate-only bind mount, and no network. There is no host-execution fallback.
- **Mission evidence and budgets.** Mission lifecycle changes are constrained by typed evidence and hard token, cost, wall-clock, tool-call, concurrency, and repair-attempt ceilings. Audit records are hash-chained; the companion event bus is versioned, idempotent, and contains only compact safe metadata.
- **Prompt-injection scanning.** User input passes through an injection scanner (`packages/hardening`) before it reaches a tool-calling planner.
- **Faithfulness / grounding harness.** The injection scanner guards the input; the faithfulness harness guards the output. It checks whether the output is supported by the sources the agent actually read, flags references to functions / files / attributes that appear in nothing it opened, scores grounding 0..1, and **abstains** when the evidence is too thin to judge. It runs in two places: (1) on the autonomous agent's **final answer** (`ronin util agent --faithfulness warn|gate`), and (2) as an **edit guard in the coding agent** - when the agent proposes a `write_file` / `edit_file` / `multi_edit`, the new code is scored against everything it read so far. In `warn` mode the score is surfaced (non-blocking) and the normal approval gate proceeds; in `gate` mode an ungrounded edit (one that references a symbol absent from every file the agent opened) is **held** with feedback so the agent must read the right file or revise - and that hold stands even under `--full-access`/yolo, where the normal approval gate would auto-approve. Opt in with `--faithfulness warn|gate` (on `ronin util agent` or the `ronin code` session) or `config set faithfulness=...`; off by default, lexical and offline, so it runs with no provider. This is a faithfulness/grounding check in the standard sense (claim decomposition plus per-claim grounding), with a code specialization for hallucinated symbols; it does not claim novelty over that literature. See [docs/FAITHFULNESS.md](docs/FAITHFULNESS.md).
- **Read-only data integrations.** The Stripe / Linear / Slack / Notion / Postgres MCP templates are read-only by default; the recommended Postgres setup uses a read-only DB role.
- **Secrets discipline.** API keys are user-supplied and stored only in local `.ronin/` (gitignored), never committed (the repo is public). PII (emails, SSNs, cards, keys) is redacted from traces before anything leaves your process.
- **Offline guarantee.** `ronin --offline` forces a local brain and removes every network-touching tool, a hard guarantee for air-gapped / privacy-sensitive work.
- **No automatic payments.** `ronin util book` is prepare-and-confirm only. It researches options and can pre-fill a form up to the payment step, then stops and hands you a summary plus the link. It never submits a payment, purchase, or final order. This is enforced in code (a payment-action guard that refuses any pay/submit/checkout step) and stated here on purpose: you always do the payment yourself.

### Running ronin for others / at scale

ronin is MIT-licensed and meant to be picked up by other people. A few notes if you're deploying it for a team:

- **Task scheduler.** `ronin util schedule` stores named tasks (a prompt plus a 5-field cron expression) in `~/.ronin/schedule.json`, lists them with their next run time, and computes which are *due* at a given instant. `ronin util schedule run-due` runs the due tasks through the agent and records each one; wire it to the system crontab to fire once a minute. The cron matcher is dependency-free and pure (unit-tested), persistence is a single JSON file, and `run-due` falls back to the offline demo brain when no key is set, so the whole thing works at $0 with no network.
- **Agent webhook gateway.** The hosted API (`apps/api`) exposes `POST /webhooks/agent`: send `{"message": "..."}` with your Bearer token and the agent runs the message and returns the reply. This is a **generic HTTP endpoint**, no Telegram/Slack account or live integration required. A chat platform can forward messages to it with a thin adapter that translates its webhook shape, but that adapter is **optional and not shipped here**. When the user has no provider key stored, the agent answers from the offline demo brain (no network egress).
- **`--yolo` / auto-accept bypasses the approval gate** and lets the model run shell commands unattended. Only use it in a sandbox or CI you trust: interactive use keeps every mutation gated.
- **Parallel sub-agents cost real tokens.** `parallel_task` / `isolated_task` / `consensus` / `bench` fan out *N* model runs at once; concurrency is capped (3–4 workers) but spend scales with the number of tasks/models: budget accordingly.
- **`isolated_task` needs a git repo** (worktrees are a git feature) and returns diffs for review rather than auto-merging: parallel changes stay reviewable.
- **Pin the installer** for reproducibility: `curl … | bash -s -- --ref v1.0.0`.

## 🧱 What's under the hood

`ronin` is the user-facing wrapper. The substance lives in **seven core packages you can also use independently** (part of a 23-package workspace), this is the engineering core:

| Package | What it does |
|---|---|
| `agent-patterns` | ReAct, Planner-Executor, Multi-Agent Supervisor, Reflexion, and a provider abstraction with streaming, 429 retry, and MCP-style metadata |
| `eval-suite` | LLM-as-a-judge plus a **SWE-bench execution harness**, golden datasets, drift detection, regression gates, and HTML reports |
| `memory` | Short-term summaries, long-term pluggable vector memory, and user preferences |
| `hardening` | Prompt-injection scanning, faithfulness/grounding, tool allowlists, approval gates, output validation, token budgets, and tracing |
| `mcp-servers` | Read-only Postgres, Stripe, Linear, Slack, Notion, Tavily, and GitHub templates |
| `cli` | The `ronin` binary: coding agent, mission control, MCP client, web tools, subagents, evaluation, media, and the **31-game arcade** (`ronin play`) |
| `deployment-templates` | Docker Compose, Modal, Vercel, and Railway |

**9,971 tests** across packages and the demo/API apps passed in the current regression suite. A `FakeProvider` makes them deterministic, offline, and free: no API calls in CI.

## Use the modules without the CLI

Build an agent in 5 lines:

```python
from ronin_agent_patterns import ReActAgent, Tool

agent = ReActAgent(
    system="You are a helpful research assistant.",
    tools=[Tool(name="search", description="...", input_schema={...}, handler=my_search)],
)
print(agent.run("What is the ReAct pattern?").output)
```

Run it on Ollama instead of Claude:

```python
from ronin_agent_patterns import OllamaProvider, ReActAgent

agent = ReActAgent(system="...", tools=[...], provider=OllamaProvider(model="llama3.1"))
```

Add an eval suite:

```python
from ronin_eval_suite import EvalSuite, Rubric, GoldenDataset

suite = EvalSuite(
    rubric=Rubric(criteria=["task_success", "faithfulness", "safety"]),
    target_runner=lambda case: agent.run(case.input).output,
)
report = suite.run(GoldenDataset.from_jsonl("./golden.jsonl"))
```

Or score the agent on **SWE-bench** — real repo bugs, graded by *running tests*
(a task is resolved iff its `FAIL_TO_PASS` tests pass and `PASS_TO_PASS` don't
regress), no Docker required:

```python
from ronin_eval_suite import SWEBenchDataset, SWEBenchHarness, make_local_git_evaluator

report = SWEBenchHarness(
    patch_runner=lambda task: agent.solve(task.problem_statement),  # -> unified diff
    evaluator=make_local_git_evaluator("./checkout"),
).run(SWEBenchDataset.from_jsonl("tasks.jsonl"))
print(report.summary["resolved_rate"])
```
```bash
ronin-eval swebench tasks.jsonl --predictions preds.jsonl --repo-root ./checkout --markdown out.md
```

> Ships the **harness**, not a published score — run it with your provider to produce numbers.

## Why ronin vs ...

| | ronin | aider | langchain | crewai |
|---|---|---|---|---|
| Terminal-native coding agent (read / edit / run, gated) | ✅ | ✅ | ❌ | ❌ |
| Provider-agnostic: Claude **and** free open-source models | ✅ | ✅ | ✅ | ✅ |
| Hand-rolled agent loop (no LangChain dependency) | ✅ | ✅ | n/a | ❌ |
| Built-in prompt-injection scanner | ✅ | ❌ | ❌ | ❌ |
| Deterministic eval suite included | ✅ | ❌ | ❌ | ❌ |
| Zero-config offline demo (`--demo`) | ✅ | ❌ | ❌ | ❌ |
| Terminal-native media (image / video / speech / vision) | ✅ | ❌ | ❌ | ❌ |

aider is the gold standard for *coding* agents; CrewAI / LangChain are general agent frameworks. ronin's angle is a **single, provider-agnostic agent** that pairs a Claude-Code-style coding experience with a tested, reusable framework underneath.

## Examples

End-to-end agents you can run today:

| Example | What it shows |
|---|---|
| [`research-agent/`](examples/research-agent/) | ReAct with a toy KB · smallest, simplest |
| [`customer-support/`](examples/customer-support/) | Supervisor + 4 sub-agents, Pydantic-validated `DraftReply`, 25-case golden dataset |
| [`code-reviewer/`](examples/code-reviewer/) | 3 specialist sub-agents (style / bugs / security) aggregating into a typed `CodeReview` |

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run python examples/customer-support/main.py "I was charged twice for my Pro plan!"
uv run python examples/code-reviewer/main.py examples/code-reviewer/sample_buggy_code.py
```

## Repository layout

```
Ronin/
├── packages/
│   ├── cli/                  # the ronin binary - the coding agent + CLI
│   ├── agent-patterns/       # core loop patterns + provider abstraction
│   ├── eval-suite/           # objective + LLM-as-judge evaluation
│   ├── memory/               # 3-layer memory
│   ├── mcp-servers/          # read-only service integrations (MCP)
│   ├── hardening/            # injection / allowlist / approval / validation
│   ├── relay/                # remote access: outbound-only connector + relay
│   └── deployment-templates/ # docker-compose, modal, vercel, railway
├── apps/
│   ├── demo/                 # AgentLab - interactive playground
│   ├── api/                  # optional FastAPI backend (scheduled briefings)
│   └── docs/                 # Mintlify documentation site
├── examples/
└── ...
```

## Documentation

- [Documentation site](apps/docs/): concepts, production checklist, ADRs (run `mintlify dev` from `apps/docs/` to preview)
- [Agent platform guide](docs/agent_platform.md): mission lifecycle, evidence gates, candidate workspaces, event bus, and remote verification workers
- [CHANGELOG.md](CHANGELOG.md): what's new
- [CONTRIBUTING.md](CONTRIBUTING.md): how to add MCP servers, providers, etc.

## License

MIT. See [LICENSE](LICENSE).

---

Built for [Claude](https://www.anthropic.com/claude). Not affiliated with Anthropic.
