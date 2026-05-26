# Changelog

All notable changes to this project will be documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows [Semantic Versioning](https://semver.org/).

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
- **`ronin code --plan`** — proposes a step-by-step plan with read-only tools, waits for your approval, *then* executes. **`ronin code --continue`** resumes this repo's last session (persisted under `.csk/sessions/`).
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
- Git-ignore the whole `.csk/` dir (was leaking `config.toml` with API keys); ignore generated `ronin_image_*` / `ronin_video_*`.

### Tests
- +29 tests (vision, set-key, api_key alias, provider-error handling, init/doctor guards). Repo total: 427 → 463.

## [0.5.0] — 2026-05-25

### Added
- **`ronin say "..."`** — text-to-speech via the OS engine (free, no key: macOS `say`, Linux espeak). Speaks aloud or saves audio with `--out`. Completes the media trio: image / video / audio.
- **`generate_image` agent tool** — `ronin code` can create images (logos, diagrams, placeholder art) and save them into the project mid-task. Free Pollinations backend, path-traversal guarded, gated like other writes.
- **`ronin video --engine replicate`** — paid real-motion text-to-video (vs. the free frame-animation engine). Creates a Replicate prediction, polls to completion, downloads the mp4. Needs `REPLICATE_API_TOKEN`; default model `minimax/video-01`, overridable with `--model owner/name`.

### Changed
- **Branding sweep**: finished the `csk` → `ronin` rename across user-facing surfaces (README, agent system prompts, web/API titles, package description) and fixed the stale old-repo-name links (now point to the `Ronin` repo) across docs, install.sh, and templates. The `.csk/` config dir, the `csk`/`ro` command aliases, and internal package/module names are unchanged (back-compat).

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
- **Briefing history + week-over-week deltas**: every `csk briefing` run auto-saves a JSON snapshot to `.csk/briefings/<date>.json`. Subsequent runs append a `_vs <last date>: MRR +$X, new subs +N, churn +M, …_` line at the bottom. `csk briefing --history` prints the full trend table. Add `--no-save` to opt out of persistence.
- **Richer demo dataset**: 8 customers, 8 subscriptions across active/canceled/past_due, 15 charges (with failures + refunds), 3 Linear teams, 12 issues across priorities — anchored to a fixed `REFERENCE_NOW` so the briefing is deterministic in demo mode.
- **PyPI publish pipeline**: `.github/workflows/release.yml` — tag a `v*` release and every workspace package + the `csk` CLI gets built and published to PyPI. One-time setup is adding a `PYPI_TOKEN` repo secret.
- README rebranded around `csk briefing` as the killer use case; secondary positioning for ad-hoc questions.

## [Unreleased]

### Added — TUI, extensibility, HTTP mode, cost tracking
- **`csk tui`**: full-screen Textual interface. Chat pane (multi-turn with in-session memory) + live trace pane, F1 help, Ctrl-L to clear, Ctrl-Q to quit. Runs the agent in a worker thread so the UI stays responsive while Claude is thinking.
- **Plugin loader**: drop a Python file in `.csk/plugins/` exposing `register_tools() -> list[Tool]` and it auto-loads. Broken plugins don't take down others — errors are surfaced via `csk plugins`. First-class extensibility without forking the kit.
- **`csk serve`**: exposes the configured agent as an HTTP API (`POST /ask`, `GET /health`). Pairs cleanly with the existing Vercel/Railway/Docker deployment templates — `docker compose up` and you have a real agent backend.
- **`csk costs`**: every `csk ask` / `csk chat` run now records token usage + cost to `.csk/usage.jsonl`. `csk costs` shows total + per-model + per-day. Pricing table for Anthropic, OpenAI, Together, Groq, Fireworks, Ollama (free).
- **`csk plugins`**: discover and inspect loaded plugins.
- **vhs tape** at `scripts/demo.tape` — declarative terminal-recording script so a 30-second GIF for the README is one `vhs scripts/demo.tape` away.

### Added — earlier in Unreleased
- **Saved queries**: `csk save NAME "..."`, `csk run NAME`, `csk queries`, `csk unsave NAME`. Persists to `.csk/queries.toml`.
- **Unified eval subcommand**: `csk eval run`/`drift` now built into the main `csk` binary (`csk-eval` still works for back-compat).
- **GitHub MCP server**: `GitHubReadOnlyTools` + `github_tools()` covering repos, issues, PRs, commits, code search. mcp-servers now ships 7 servers.
- **End-to-end examples**: `customer-support/` (Supervisor + 4 specialists + Pydantic `DraftReply`) and `code-reviewer/` (style/bugs/security specialists + typed `CodeReview`).
- **Tavily web-search MCP server** — `TavilyTools` + `tavily_tools()` factory.

### Repo stats
- 218 tests, green on every push.

## [0.1.0] — 2026-05-08

### Added — `csk` CLI

- New `ro-claude-kit-cli` package shipping the `csk` binary.
- Subcommands: `csk init`, `csk ask`, `csk chat`, `csk tools`, `csk doctor`, `csk version`.
- Demo mode (`csk init --demo`) ships fake Stripe + Linear data; runs zero-config.
- Offline `demo_brain` keyword router so `csk ask` works without any API key.
- Rich terminal output: tables, panels, spinners.
- Prompt-injection scanning at the CLI boundary before any tool call.
- Auto-loads `.csk/config.toml` (project-local) or `~/.config/csk/config.toml` (user-global); env-var overrides win over file values.

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
