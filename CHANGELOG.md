# Changelog

All notable changes to this project will be documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added — in-session UX (free-first controls + theming)
- **`/provider`** — list every provider with a free/paid tag and key health (keyless / key set / needs key), and switch with `/provider <name>` (keeps the saved per-provider key, resets to the new provider's default model). A live provider-health view without leaving the session.
- **`/free [on]`** — show whether the current model runs at **$0** and which free providers are ready; `/free on` switches to the best free provider you can run right now (a free tier you hold a key for, else the keyless local brain).
- **`/theme [name]`** — switch the code/diff syntax-highlight theme from a curated set of dark Pygments styles. Applies **live** (renderers re-read the theme each turn) and persists to config (new `theme` field), reapplied at session start.

### Fixed
- **Ronin now knows its own features.** A free model asked "does ronin have games?" answered *no* — but `ronin play` ships a **31-game arcade**. The agent's front-door prompt listed only its tools, never ronin's own features, so the model guessed wrong. Added a factual ABOUT RONIN block (arcade + free-first providers + headline commands) with a "don't guess about yourself" directive; a drift-guard test keeps the stated game count equal to `len(GAMES)`.

**Web research and page watches** — both free and keyless, both offline-tested.

### Added — SWE-bench harness (eval-suite)
- **Execution-based coding eval** alongside the LLM-as-judge suite. A task is *resolved* iff every `FAIL_TO_PASS` test passes and every `PASS_TO_PASS` test still passes after the agent's patch. `SWEBenchHarness` is execution-agnostic — plug in a `patch_runner` (your agent) and an `evaluator`; `make_local_git_evaluator` is a Docker-free reference over a local checkout. `SWEBenchDataset` loads the official JSONL (UPPER_SNAKE aliases + JSON-string test lists), with `.subset()`/`.repos()` for smoke and per-repo runs.
- **CLI** — `csk-eval swebench <tasks> --predictions <preds> --repo-root <path>` scores a standard predictions file and writes a JSON (and optional `--markdown`) report; `csk-eval swebench-compare <a> <b>` exits non-zero on any resolved→unresolved regression. `oracle_runner` self-validates the environment with gold patches; `compare_swebench`/`render_swebench_markdown` diff and present runs. Harness needs no API key, Docker, or network — 38 offline tests.

### Added — web research
- **`ronin research "<question>"`** — search the web and answer, with sources. Runs the read-only agent with the keyless web tools (`web_search` + `fetch_url`, DuckDuckGo HTML, no API key) so it searches, reads the most relevant pages, and answers. If no model is configured it degrades to a raw web search so you still get links. The agent run is injectable, so the command is unit-tested with no model and no network.

### Added — page watches
- **Watch a page from Telegram** — `watch <url>`, `watch <url> for <keyword>`, `list watches`, `cancel watch N`. Stored at `~/.ronin/watches.json`. The bot re-checks each watch on its existing poll tick (throttled, default every 15 minutes), hashes the watched slice (the whole readable page, or only lines containing the keyword), and pings you when it changes. The first check records a baseline (no false ping); a fetch failure keeps the old snapshot. Reminders and watches share ONE due-checking loop — no second daemon. Hashing, parsing, and the throttle are pure and unit-tested; the fetch and the Telegram send are injected so the tests are fully offline.

## [0.58.0] — 2026-06-04

**Quality gates** — four objective, CI-friendly checks aimed at the code you're about to ship, in the same "outcome over LLM-judge" spirit as `eval`/`kaizen`. Each ships with a pure, unit-tested core. **1002 tests.**

### Added — quality gates
- **`ronin mutants <file>`** — mutation testing. Injects one-operator faults (`==`→`!=`, `and`→`or`, relational boundary toggles, `True`→`False`, `+`→`-`), runs your suite against each, and reports the mutants that **survived** (tests that catch nothing). Requires a green baseline; restores the original file in every path. Reports a mutation score and exits non-zero on survivors.
- **`ronin radius`** — blast radius. Builds the repo's Python import graph (via `ast`), walks it backwards from your uncommitted diff to every transitively-dependent module, and surfaces the test modules in that radius. `--run` executes just those tests.
- **`ronin flake "<cmd>" -n N`** — flaky-test hunter. Runs a test command N times, diffs the failure sets, and ranks tests that flip green↔red — distinguishing flaky from stably-broken.
- **`ronin guard`** — scope-creep / leftover guard. Scans added diff lines for debug/secret leftovers (`breakpoint()`, `console.log`, merge markers, AWS keys, `TODO/FIXME`) and, with `--intent`, runs a read-only LLM check for files that drift from the task. Non-zero exit on high-severity findings.

## [0.56.0] — 2026-06-02

Full rebrand to **ronin** (internals, config dir, packages — no more "claude-kit/csk"), plus a learning/trust pass: the router now learns, the agent can abstain, and ronin reaches GitHub. **803 → 821 tests.**

### Added — learns & adapts
- **Self-tuning Router** — after each routed turn the outcome (clean finish vs error/block) is recorded per (tier, provider) in `.ronin/router_stats.json`. When the cheap blade proves unreliable for a tier *in this repo* (≥4 samples, <60% success), the router escalates that tier to the strong blade on its own.
- **Cost Router wired live** — `route_fast`/`route_strong` now actually switch the per-turn provider; the footer shows `💰 cost · saved $Y vs all-<provider> · N/M turns free`.

### Added — trust
- **Sentinel mode** (`ronin --sentinel`) — abstain over bluff: every reply ends with `CONFIDENCE: high|medium|low` and names what it's unsure about. Directly counters confident hallucination.
- **Escalation ladder** — low confidence on a cheap blade retries the turn once on the strong blade (uncertainty escalates instead of shipping).

### Added — GitHub
- **`ronin review --pr N`** — review a GitHub PR (diff via `gh`); `--comment` posts the review back onto it.
- **`ronin triage`** — read open issues and draft labels + priority + a first response (read-only).

### Changed
- Renamed everything from "Claude kit / csk" to **ronin**: `ronin_*` modules, `RoninConfig`, `ronin-*` packages, `.ronin/` config dir (with a safe one-time `.csk`→`.ronin` merge-migration), project dir `~/ronin`, dropped the `csk` alias.

## [0.55.0] — 2026-06-02

ronin's signature pass — six provider-agnostic things a single-vendor agent
**structurally can't do**, each shipped with its pure core unit-tested. **741 → 787 tests.**

### Added — "only ronin can do this"
- **Kaizen** (`ronin kaizen [goal]`) — the self-forging agent. Finds a weakness in ronin's own source (FIXME/BUG/XXX/TODO markers, strongest first), drafts a fix in an **isolated git worktree**, and runs the project's **own test suite as an objective fitness gate** — the diff only reaches your tree if the tests pass there. Point it at a free provider and it improves code for $0. **`--duel <provider>`** adds a cross-vendor gate: a rival model red-teams the proven diff before you approve, and a BLOCK overrides `--yes`. So a self-improvement can clear both an objective gate (tests) *and* an adversarial one.
- **The Dojo** (`ronin dojo "<task>" -m anthropic,gemini,cerebras`) — rival models each attempt the **same** change in **parallel isolated worktrees**; a judge scores the diffs and crowns a winner you can apply. Claude vs Gemini vs DeepSeek, swinging at one problem.
- **Ronin Duel** (`ronin duel --against gemini`) — a **different** provider adversarially red-teams your git diff and returns structured blockers. The author model is a poor judge of its own code; a rival vendor isn't. Exits non-zero on BLOCK (CI-friendly).
- **Scout → Strike** (`ronin code --scout "<task>"`) — read-only recon runs on a free/cheap blade (`route_fast`), then a strong blade (`route_strong`) executes only the edits. Frontier quality where it matters, free everywhere else.
- **Bushido** — a global `~/.ronin/bushido.md` code of honor the agent carries into **every** repo (folded in before project memory; the repo always wins). `remember_preference` tool persists a standing cross-project convention.
- **Muscle Memory** — the agent crystallizes a solved workflow into a reusable repo-local `/skill` (`.ronin/commands/*.md`) via `crystallize_skill` — immediately re-runnable, committed with the code, compounding over time.
- **Cost Router** — pricing table ($/Mtok, free providers = $0) + a `CostLedger` that tracks spend and "saved vs all-anthropic"; routing resolves cross-provider targets so a simple turn can run on a **free** provider, not just a cheaper model. **Wired live into both session loops**: with `route_fast`/`route_strong` set, each turn runs on the routed (possibly free) provider and the footer shows `💰 cost: $X · saved $Y vs all-<provider> · N/M turns free`.

## [0.54.0] — 2026-06-02

Closing the gap between what the README promises and what `ronin code` ships, plus VCS awareness and an opt-in unsandboxed mode. **730 → 741 tests.**

### Added — capabilities
- **Web search in `ronin code`.** The coding agent now gets `web_search` + `fetch_url` directly (they were only on the unified/chat surface before, despite the README listing them under `ronin code`). Read-only, so they work in plan mode too; `--offline` still strips them. Built once in `run_code_agent` with a name-dedup guard so `extra_tools` can't double-register.
- **`@`-URL mentions.** `@https://…` in a request now fetches that page's readable text into context — the web counterpart of `@file`. Skipped under `--offline`; failed fetches are left as-is.
- **Read-only git tools** (`git_status` / `git_diff` / `git_log`) — the agent reasons about VCS state instead of blindly shelling out (and tripping the run_command gate for a harmless read). Mutating git stays in the gated `/commit` & `/pr` commands.
- **Full-access mode** (`--full-access` / `--god-mode`, opt-in) — lifts the filesystem sandbox (reach beyond the project root), auto-approves every edit/command, and gives `run_command` a longer timeout + bigger output caps. Prints a ⚠ banner; off by default. The sandbox seam is now pinned by tests in both directions (in-root allowed, escape blocked unless full-access).

## [0.53.0] — 2026-06-01

A reliability + UX pass on top of v0.52, plus three capability additions. **636 → 730 tests.**

### Added — experience
- **Full-screen TUI as a real coding surface (opt-in via `ronin --tui`).** Rewrote it to drive `run_code_agent` off the UI thread with the whole toolbelt, stream tokens, show a live `⏺` tool trace, and gate sensitive actions (write/edit/run/…) behind an approval modal bridged across threads. The **default stays the minimal, Claude-Code-style inline REPL** (scrollback + bordered input box).
- **Type-ahead input queue** in the inline REPL — messages typed while the agent works are captured (via a cancellable `select` reader) and run as the next turn. No-op on non-TTY.
- **Clarifying questions** — an `ask_user` tool lets the agent ask one sharp question before acting on an ambiguous task (interactive sessions only).

### Added — capabilities
- **Embeddings RAG** (`semantic_search`) — optional semantic code search via Ollama (local) or any OpenAI-compatible `/embeddings`, cosine + content-hash disk cache. Exposed only when a backend exists; BM25 `repo_map` stays the zero-config default.
- **Auto context engineering** — each interactive turn injects the most relevant files (paths + symbol outlines) into the prompt; non-blocking (cold index builds in the background) and self-gating.
- **Background processes** (`run_background` / `background_logs` / `background_status` / `stop_background`), **checkpoint & rewind** (whole-workspace snapshot/rollback), and **vision-in-the-loop** (`screenshot` + `look_at`).

### Fixed — reliability (built for free models)
- **Bulletproof tool-calling** — near-miss argument names are remapped to the handler's real params (path↔directory, cmd↔command, …), unknown extras dropped, and an argument mismatch returns a coaching error with the expected signature instead of a raw `TypeError`.
- **Context/token management** — a per-result cap stops one giant tool result from blowing the window, and compaction triggers far earlier off-Anthropic (28k vs 120k) where free models have smaller windows.
- **Per-provider API keys** — `/login openai` no longer clobbers your cerebras key; each provider keeps its own (`provider_keys`).
- **Tool ignore set** now covers `venv` (not just `.venv`), `vendor`, `dist`, `build`, `target`, … — fixes list/search dumping a whole virtualenv into context (the 168s/19.9k-token turn). `path` accepted as an alias for `directory`.
- **Rate-limit backoff is now visible** — a 429 retry shows "⏳ retrying in Ns (Ctrl+C to stop)" instead of a silent ~60s freeze.

## [0.52.0] — 2026-06-01

A big pass adding capabilities a single-vendor agent structurally can't have, plus three coding-agent upgrades. **540 → 636 tests.**

### Added — "beyond Claude Code" (provider-agnostic superpowers)
- **Multi-model consensus** (`ronin consensus "<task>" -m a,b,c`) — run the same question on several models in parallel, then a judge model synthesizes one cross-checked answer with a panel-agreement note. Read-only. (`consensus.py`, +5 tests.)
- **Cross-provider failover** — new `FailoverProvider` (agent-patterns) + a `failover` config list; a turn that rate-limits/errors on the primary transparently continues on the next provider. Streamed tokens are never silently re-answered. (+6 provider tests, +4 wiring tests.)
- **Fully offline mode** (`ronin --offline`) — forces a local brain (Ollama) and strips every network tool, for zero-egress / air-gapped work. (`offline.py`, +6 tests.)
- **Eval-driven model bake-off** (`ronin bench -m a,b,c`) — runs the objective eval battery across models and recommends the cheapest one that clears a quality bar. (`bench.py`, +5 tests.)

### Added — coding-agent upgrades
- **Anthropic prompt caching** — `cache_control` breakpoints on the system + tools prefix (on by default); cache-read tokens surface in usage and show as `⚡N cached` in the status line. (+3 tests.)
- **Semantic code intelligence** — `diagnostics` / `definition` / `references` tools backed by real language servers (pyright, ts-language-server, gopls, rust-analyzer) over JSON-RPC, with graceful "install X" fallback. (`lsp.py`, +17 tests.)
- **Parallel mutating sub-agents** — `parallel_task` (concurrent read-only fan-out) and `isolated_task` (parallel editing agents, each in its own git worktree so changes can't collide; returns reviewable diffs). (`worktree.py`, +12 tests.)

### Docs
- README: new "Beyond Claude Code" section, "Running ronin for others / at scale" safety notes, updated command table + tool list.

## [0.13.0 – 0.24.0] — 2026-05-27

A large pass turning ronin into a Claude-Code-grade agent that runs on free models.

### Added — Claude-Code-grade session UI
- **Bordered input box** you type inside, with `↑/↓` history and `/`+TAB slash-command completion.
- **Streaming Markdown** — replies render live (bold, headings, lists, syntax-highlighted code) instead of raw `**`/`#`.
- **`● Verb(target)` / `↳ result`** tool lines, **syntax-highlighted diffs** in approval prompts, a per-turn **status line** (provider · model · tokens · time).
- Animated activity panda on launch (dancing / running / playing / playing football / sleeping) + a real half-block panda renderer.

### Added — providers & resilience
- **Free providers**: Gemini, Cerebras, OpenRouter (plus Groq) — no credit card. **`/login <provider>`** (masked, in-session), **`/model` / `/models`** to switch models without re-entering the key.
- **429/5xx auto-retry** with backoff (rides over free per-minute caps); Gemini thinking-model `thought_signature` round-tripping so tool calls work.
- Start a message with a folder path to switch the working directory into it.

### Added — capabilities
- **`ronin eval`** — objective agent-quality scoring across providers (no LLM judge); golden-dataset `run`/`drift` kept as subcommands.
- **MCP client** — connect any MCP server (`ronin mcp add/list`); discovered tools auto-join the agent.
- **`web_search` / `fetch_url`** tools (free, no key) and a read-only **`task` subagent** tool.

## [0.12.0] — 2026-05-26

### Added — automatic memory (remembers everything, no prompting)
- After every turn, ronin now **auto-extracts durable facts about you** from the exchange (name, stack, projects, preferences, goals) and saves them to long-term memory — in a **background thread**, so it never adds latency. You no longer have to say "remember this"; it just remembers.
- Best-effort and crash-proof: extraction failures (rate limits, parse errors) are swallowed silently — memory can never break a turn.
- Wired into both the unified `ronin` and `ronin chat`, on top of the explicit `remember` tool and the `ronin memory` view/clear command.
- 4 tests (JSON parse, saves facts, silent-on-error, empty list). Repo total: 494 → 498.

## [0.11.0] — 2026-05-26

### Added — persistent cross-session memory
- ronin now **remembers you across sessions**. Durable facts/preferences (your name, stack, the repos you work in, how you like things) are saved to a user-global `~/.ronin/memory.json` and auto-injected into the system prompt on every future run — a brand-new `ronin` already knows you.
- The agent saves facts itself via a new **`remember`** tool; recall is by injecting the most recent facts (no vector DB, no extra deps — built on the kit's `memory` package).
- **`ronin memory`** views what it knows (`--add` / `--clear`); `/memory` in-session shows it; a `🧠 N things remembered` note appears on launch.
- 7 tests (add/load/dedupe, prompt block, remember tool, forget, CLI, agent persists across a session). Repo total: 487 → 494.

## [0.10.0] — 2026-05-26

### Changed — soft, premium UI
- A **gradient `✦ ronin` wordmark** (magenta→violet→indigo) and a **soft pastel palette** (muted rose/green/teal/slate instead of hard ANSI) across the CLI.
- The interactive session opens with a **soft rounded welcome card** (gradient mark · cwd · model · mode · hint).
- A gentle **"thinking…" spinner** animates until the first token, replies are headed by the gradient ronin avatar, and turns are separated by a soft divider.
- `theme.py` gained `gradient_text()`; tool/result lines use the soft palette.

## [0.9.0] — 2026-05-26

### Changed — one unified front door
- **Bare `ronin` is now ONE assistant that does everything** in a single conversation: talk, write & run code (edits + shell commands gated with diffs/approval), generate images/video/speech, and query connected data — all on the same agent. No more choosing between "chat" and "code"; ask for anything in plain language and it routes to the right capability.
- `ronin chat` (talk + media only) and `ronin code` (pure coding agent) remain as focused modes.
- `run_code_agent` gained `extra_tools` / `extra_system` / `include_image_tool` so the unified session layers media + data tools onto the coding agent's machinery (streaming, diffs, approval gate, todo tracker, project memory, `@`-mentions, `/`-commands).

### Fixed
- Image generation no longer triggers an approval prompt — making a picture is a free, low-risk action; only file edits and shell commands are gated.
- The chat no longer generates an image when asked to *write code* about images (tightened intent routing).

### Tests
- +3 tests (unified session has code+media+data tools, generates images, writes code). Repo total: 483 → 486.

## [0.8.0] — 2026-05-26

### Added — Claude-Code parity for the coding loop
- **`@path` file mentions** — reference files in your request (e.g. `ronin code "explain @main.py"`) and their contents are inlined into context (path-traversal guarded).
- **Bare `ronin` opens the coding agent in a repo** — inside a code project (`.git`/`pyproject.toml`/`package.json`/`RONIN.md`/…) typing `ronin` drops into the coding session (Claude Code's default); outside one it's the data/media chat. `ronin chat` always forces chat; `ronin code` always forces the agent.
- **`ronin code --plan`** — proposes a step-by-step plan with read-only tools, waits for your approval, *then* executes. **`ronin code --continue`** resumes this repo's last session (persisted under `.ronin/sessions/`).
- **New tools**: `glob` (find files by pattern) and `multi_edit` (several surgical replacements in one approved, all-or-nothing step). `multi_edit` is gated like other writes.
- **Markdown rendering** — the chat and one-shot answers render as rich Markdown (headings, lists, syntax-highlighted code), like Claude Code.
- 13 tests (mentions, glob, multi_edit all-or-nothing, repo detection, session round-trip, read-only/plan tool filtering). Repo total: 471 → 483.

## [0.7.0] — 2026-05-26

### Added — `ronin explain` (the onboarding killer feature)
- **`ronin explain <path>`** — point it at any unfamiliar file/module/repo and it produces (1) a plain-English explanation (big picture → key pieces → data flow), (2) an auto-generated **Mermaid architecture diagram** that renders on GitHub / pastes into a README, and (3) optional **voice narration** (`--speak`). Read-only: it explores with read_file/list_files/search_files and never mutates. `--out file.md` writes the explanation + diagram.
- The differentiator: a pure coding agent *explains* — ronin explains, **draws it**, and **speaks it**, because it has a diagram generator and a voice. Onboard to a codebase in minutes.
- 8 tests (mermaid extraction, read-only-tools-only, no-diagram flag, injection block, `--out`, no-key guard). Repo total: 463 → 471.

## [0.6.0] — 2026-05-25

### Added
- **`ronin see <image> "<question>"`** — vision. Ask Claude (or any vision model) about a local image; ronin can now both *generate* and *understand* pictures. Shows the image inline, then the answer. Anthropic + OpenAI-compatible vision formats.
- **`ronin set-key [--provider X]`** — a friendly key setter: masked input, then a safe preview (length + `gsk_…last4` + verdict) and a hard refusal of `>=80`-char values, so the blind double-paste that produces a broken 450-char key can't happen silently. `ronin init` now echoes the same preview after the hidden prompt.
- Config accepts a provider-neutral **`api_key`** in the TOML as an alias for `openai_api_key` (the latter name confused Groq/Together users).
- **Demo assets**: a `vhs` tape at `docs/demo/demo.tape` (regenerates a walkthrough GIF, keyless) and a sample generated image in the README.

### Fixed
- The interactive chat and one-shot ask no longer crash on a provider error — they show a clean, actionable message (`_friendly_provider_error`: 401/403/429/connection).
- `ronin init` rejects bogus model answers (`yes`/`no`/…) and falls back to the provider default; `ronin doctor --check` does a live key+model ping instead of a misleading "ok".
- Git-ignore the whole `.ronin/` dir (was leaking `config.toml` with API keys); ignore generated `ronin_image_*` / `ronin_video_*`.

### Tests
- +29 tests (vision, set-key, api_key alias, provider-error handling, init/doctor guards). Repo total: 427 → 463.

## [0.5.0] — 2026-05-25

### Added
- **`ronin say "..."`** — text-to-speech via the OS engine (free, no key: macOS `say`, Linux espeak). Speaks aloud or saves audio with `--out`. Completes the media trio: image / video / audio.
- **`generate_image` agent tool** — `ronin code` can create images (logos, diagrams, placeholder art) and save them into the project mid-task. Free Pollinations backend, path-traversal guarded, gated like other writes.
- **`ronin video --engine replicate`** — paid real-motion text-to-video (vs. the free frame-animation engine). Creates a Replicate prediction, polls to completion, downloads the mp4. Needs `REPLICATE_API_TOKEN`; default model `minimax/video-01`, overridable with `--model owner/name`.

### Changed
- **Branding sweep**: finished the `csk` → `ronin` rename across user-facing surfaces (README, agent system prompts, web/API titles, package description) and fixed the stale old-repo-name links (now point to the `Ronin` repo) across docs, install.sh, and templates. The `.ronin/` config dir, the `csk`/`ro` command aliases, and internal package/module names are unchanged (back-compat).

### Tests
- +20 tests (audio, agent image tool, Replicate create/poll/download). Repo total: 408 → 427.

## [0.4.0] — 2026-05-25

### Added — media generation (terminal-native)

- **`ronin image "..."`** — text-to-image that displays in the terminal. Default backend is **Pollinations** (free, no API key), with an **OpenAI** backend (`gpt-image-1`, needs `OPENAI_API_KEY`) for higher quality. Shows inline on iTerm2, via `chafa`/`viu`/`imgcat` if installed, else opens in the system viewer. `--size`, `--seed`, `--model`, `--out`, `--backend`.
- **`ronin video "..."`** — free text-to-video. Generates N AI frames (incrementing the seed) and stitches them into a real `.mp4` with `ffmpeg`, previews the first frame inline, and opens the clip. Honest framing: this is frame-animation, not Sora-grade real-motion — the per-frame backend is pluggable so a paid motion provider can slot in later. `--frames`, `--fps`, `--size`, `--seed`.
- stdlib `urllib` only (no new dependency). **+15 tests** (both image backends, missing-key/bad-backend/bad-size guards, display fallback, video frame seeding + ffmpeg invocation/failure guards, CLI paths). Repo total: 393 → 408.

## [0.3.0] — 2026-05-25

### Added — `ronin code` now feels like Claude Code

- **Token streaming**: providers gained a `stream()` method (Anthropic via `messages.stream`, OpenAI-compatible via SSE with tool-call delta accumulation). `ronin code` / `ronin agent` now print the model's text token-by-token with tool activity inline, instead of blocking silently and dumping the whole turn at once. `complete()` stays for non-streaming callers; providers without native streaming fall back automatically.
- **Live todo/plan tracker**: for any 3+ step task the agent maintains a checklist via an `update_todos` tool (exactly one item in-progress, items flipped to completed as it goes), rendered inline as `✓ / ▶ / ☐`.
- **Project memory**: auto-loads `RONIN.md` / `CLAUDE.md` / `AGENTS.md` from the repo root into the system prompt so the agent follows your conventions. `ronin code --init` scaffolds a template. Capped at 8k chars.
- **In-session slash commands**: `/help`, `/clear`, `/undo`, `/diff` (colorized working-tree git diff), `/model`, `/memory`, `/init`, `/tools`, `/quit` (both `/cmd` and `:cmd` accepted).
- **Animated panda mascot** on launch — a small kaomoji panda that dances / runs / plays / sleeps (`ronin panda [activity]`), replacing the static block-art face that broke on some terminals.
- **+29 tests** (stream contract, delta forwarding, tool loop under streaming, todo tracker, project memory, every slash command). Repo total: 364 → 393.

## [Unreleased]

### Added — hosted SaaS backend (`apps/api`)

- **FastAPI service** behind a Bearer-token API: `POST /signup` (returns a one-time `csk_*` token), `POST /connections` (encrypted credential upload), `GET /connections` (names only, never secrets), `POST /briefings` (run-now), `GET /briefings` (history), `POST /briefings/schedule` (weekly cron).
- **Encryption at rest** for stored third-party credentials — Fernet (AES-128-CBC + HMAC-SHA256), key from `FERNET_KEY` env var. API tokens are SHA-256-hashed before storage.
- **SQLAlchemy 2.0 models** for users, service_connections (per-user encrypted), briefing_runs (full Markdown + key metrics for trending), schedules. Defaults to sqlite for dev; flip `DATABASE_URL` to a Postgres URI for prod.
- **Standalone worker** (`python -m csk_api.worker`) — polls for due users every N seconds, runs their briefing, marks the schedule as ran. `--once` for a single tick, `--interval N` for the loop.
- **Deploy infra**: `Dockerfile` + `railway.json` for one-click Railway deploys. Healthcheck on `/health`. Restart-on-failure policy.
- **14 tests** (auth, connection round-trip, secret never returned, briefing creation, history growth, week-over-week delta, schedule round-trip, worker tick). Repo total: 242 → 256.

This unblocks the "csk as SaaS, $19/mo" path: hosted scheduling, Slack delivery on cron, encrypted multi-tenant credentials. Frontend + OAuth + Stripe billing are deliberate follow-ups.

## [0.2.0] — 2026-05-11

### Added — the headline command + PyPI publish
- **`csk briefing`**: the Monday-morning founder briefing as a CLI. Revenue (MRR/ARR, new/churned this week), payments (succeeded/failed/refunded, past-due subs), engineering (urgent/high open, in-progress), and computed action items. Renders Markdown — paste into Slack/email/docs. Runs offline against demo data; runs against your real Stripe/Linear data once configured. This is now the README hero.
- **`csk briefing --slack <channel>`**: post the briefing straight to a Slack channel via `chat.postMessage`. Requires a bot token with `chat:write`. Converts the Markdown subset to Slack mrkdwn (`**bold**` → `*bold*`, headings → `*Heading*`).
- **Briefing history + week-over-week deltas**: every `csk briefing` run auto-saves a JSON snapshot to `.ronin/briefings/<date>.json`. Subsequent runs append a `_vs <last date>: MRR +$X, new subs +N, churn +M, …_` line at the bottom. `csk briefing --history` prints the full trend table. Add `--no-save` to opt out of persistence.
- **Richer demo dataset**: 8 customers, 8 subscriptions across active/canceled/past_due, 15 charges (with failures + refunds), 3 Linear teams, 12 issues across priorities — anchored to a fixed `REFERENCE_NOW` so the briefing is deterministic in demo mode.
- **PyPI publish pipeline**: `.github/workflows/release.yml` — tag a `v*` release and every workspace package + the `csk` CLI gets built and published to PyPI. One-time setup is adding a `PYPI_TOKEN` repo secret.
- README rebranded around `csk briefing` as the killer use case; secondary positioning for ad-hoc questions.

## [Unreleased]

### Added — TUI, extensibility, HTTP mode, cost tracking
- **`csk tui`**: full-screen Textual interface. Chat pane (multi-turn with in-session memory) + live trace pane, F1 help, Ctrl-L to clear, Ctrl-Q to quit. Runs the agent in a worker thread so the UI stays responsive while Claude is thinking.
- **Plugin loader**: drop a Python file in `.ronin/plugins/` exposing `register_tools() -> list[Tool]` and it auto-loads. Broken plugins don't take down others — errors are surfaced via `csk plugins`. First-class extensibility without forking the kit.
- **`csk serve`**: exposes the configured agent as an HTTP API (`POST /ask`, `GET /health`). Pairs cleanly with the existing Vercel/Railway/Docker deployment templates — `docker compose up` and you have a real agent backend.
- **`csk costs`**: every `csk ask` / `csk chat` run now records token usage + cost to `.ronin/usage.jsonl`. `csk costs` shows total + per-model + per-day. Pricing table for Anthropic, OpenAI, Together, Groq, Fireworks, Ollama (free).
- **`csk plugins`**: discover and inspect loaded plugins.
- **vhs tape** at `scripts/demo.tape` — declarative terminal-recording script so a 30-second GIF for the README is one `vhs scripts/demo.tape` away.

### Added — earlier in Unreleased
- **Saved queries**: `csk save NAME "..."`, `csk run NAME`, `csk queries`, `csk unsave NAME`. Persists to `.ronin/queries.toml`.
- **Unified eval subcommand**: `csk eval run`/`drift` now built into the main `csk` binary (`csk-eval` still works for back-compat).
- **GitHub MCP server**: `GitHubReadOnlyTools` + `github_tools()` covering repos, issues, PRs, commits, code search. mcp-servers now ships 7 servers.
- **End-to-end examples**: `customer-support/` (Supervisor + 4 specialists + Pydantic `DraftReply`) and `code-reviewer/` (style/bugs/security specialists + typed `CodeReview`).
- **Tavily web-search MCP server** — `TavilyTools` + `tavily_tools()` factory.

### Repo stats
- 218 tests, green on every push.

## [0.1.0] — 2026-05-08

### Added — `csk` CLI

- New `ronin-cli` package shipping the `csk` binary.
- Subcommands: `csk init`, `csk ask`, `csk chat`, `csk tools`, `csk doctor`, `csk version`.
- Demo mode (`csk init --demo`) ships fake Stripe + Linear data; runs zero-config.
- Offline `demo_brain` keyword router so `csk ask` works without any API key.
- Rich terminal output: tables, panels, spinners.
- Prompt-injection scanning at the CLI boundary before any tool call.
- Auto-loads `.ronin/config.toml` (project-local) or `~/.config/csk/config.toml` (user-global); env-var overrides win over file values.

### Added — multi-provider support

- New `LLMProvider` abstraction in `agent-patterns`. Every pattern (`ReActAgent`, `PlannerExecutorAgent`, `SupervisorAgent`, `ReflexionAgent`) now accepts a `provider` kwarg.
- `AnthropicProvider` (default) — Claude.
- `OpenAICompatProvider` — OpenAI, Ollama, Together, Groq, Fireworks, vLLM, llama.cpp server, LM Studio, anything with `/chat/completions`.
- `OllamaProvider` convenience subclass — defaults to `http://localhost:11434/v1`, no API key needed.
- `FakeProvider` for tests.

### Added — modules

- `agent-patterns`: ReAct, Planner-Executor, Multi-Agent Supervisor, Reflexion.
- `eval-suite`: LLM-as-a-judge, golden datasets, drift detection, HTML reports, `csk-eval` CLI.
- `memory`: short-term (rolling summary), long-term (pluggable vector backend), user preferences.
- `hardening`: prompt-injection scanner, output-leak scanner, tool allowlist, approval gates, output validator with retry, PII-redacted tracing.
- `mcp-servers`: read-only Postgres, Stripe, Linear, Slack, Notion templates.
- `deployment-templates`: Docker Compose, Modal, Vercel, Railway one-click deploys.
- `apps/demo`: AgentLab — interactive FastAPI playground for all four agent patterns.
- `apps/docs`: Mintlify documentation site with eight content pages and Mermaid diagrams.

### Tests
- 152 tests across all packages, all passing on every push.

[Unreleased]: https://github.com/rohithkandula19/Ronin/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rohithkandula19/Ronin/releases/tag/v0.1.0
