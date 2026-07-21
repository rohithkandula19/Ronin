# Ronin AI OS — baseline audit (start of `feat/ronin-ai-os-foundation`)

Recorded before any implementation change. Branch point: `main` @ `3b49dc1`.

## Test baseline

`uv run --frozen pytest packages/... apps/... training -q` at the branch point:
**3,642 passed, 6 skipped, 0 failures.** There are no pre-existing failures to
carry; any red test on this branch is new work's responsibility.

## What exists today (inventory)

### Runtime (packages/cli + packages/agent-patterns) — the crown jewels
- **Agent loop:** `ReActAgent.run()` (`agent-patterns/.../react.py`) — neutral
  Message history, streaming, `before_tool` gate contract
  (`(name, args) -> bool | str`, string = deny-with-feedback).
- **The web-safe seam:** `run_code_agent(...)` (`cli/.../code_mode.py:657`)
  accepts `gate_cb`, `on_text_cb`, `on_step_cb`, `on_reset_cb` — a headless
  caller drives approvals and streaming without a Console. Crucially the
  destructive floor (`approvals.is_floored_tool_call`) and `.ronin/settings.json`
  deny-rules are enforced **in-process, wrapped around any supplied gate_cb** —
  a frontend cannot waive them; `console=None` without `gate_cb` = default-deny.
  A fail-closed drift guard raises if a mutating tool ever reaches the toolbelt
  ungated.
- **Tools:** `build_code_tools` (read/search/edit/run, path-escape refusal),
  `build_background_tools` (run_background/logs/status/stop), checkpoint tools
  (two layers: agent `refs/ronin/checkpoints/*` + user session safety-net with
  copy fallback), todo/plan tool, MCP client tools (all `sensitive=True`,
  readOnlyHint deliberately ignored), plugins (trust-gated).
- **Sandboxes:** local / docker / ssh / macOS seatbelt, fail-closed
  (`backends.py`); Linux landlock referenced but NOT implemented.
- **Multi-agent:** orchestrator (worktree-isolated, ungated inside the
  worktree → diff is pending-review), pipeline (gated stages, resumable,
  pydantic state), consensus, dojo, duel, scout/strike, review/fix/explain.
- **Providers:** `PROVIDER_PRESETS` (anthropic, openai, ollama, local/embedded,
  together, groq, fireworks, gemini, cerebras, openrouter, custom),
  failover chains, cost ledger (`cost.py`), two routers (interactive
  cost-router + `ronin route` learned router), offline mode (forces local
  brain, strips network tools), free mode. `EmbeddedProvider` = keyless local
  inference (mlx / llama.cpp), `$RONIN_ADAPTER` LoRA loading, strict
  `<tool_call>` parsing.
- **Memory (product):** `memory_store.py` — `~/.ronin/memory.json`, 0600,
  secret-scanner refusal at the store, recall via stemmed Jaccard, LLM
  auto-extract; project memory = RONIN.md/CLAUDE.md/AGENTS.md; sessions
  archived to `.ronin/sessions/*.json` (text transcript only — structured
  message history is NOT persisted). **No org scoping, no industry scoping,
  no retention policy, no training-eligibility flags.** This is the gap Vault
  addresses.
- **Memory (library):** `packages/memory` — short-term compression, pluggable
  long-term backend, preferences. Namespace-string scoping only.

### Evaluation
- `packages/eval-suite`: LLM-judge suites + SWE-bench harness (execution-based,
  disposable-tree guard), drift detection, `csk-eval` CLI.
- `training/ronin_training/eval_runner.py`: deterministic protocol evals with
  runtime-parity `<tool_call>` extraction (independent of eval-suite).
- No unified per-industry required-suite gate exists. That's this program's
  evaluation-framework gap.

### Fine-tuning (two stacks)
- `packages/cli/finetune.py`: session-mining → Alpaca JSONL (PII-scrubbed) →
  unsloth QLoRA `train.py` + Ollama Modelfile. Generates; does not train.
- `training/`: volume-driven MLX pipeline — dataset builder w/ schema+registry
  validation, license enum banning proprietary targets, coverage floors,
  protocol evals, honest reports. **Forge extends this stack.** Gaps: no
  per-item provenance/consent record, no redaction review workflow, no job
  state machine, no adapter registry.

### Apps
- `apps/api` (FastAPI, package dir `csk_api`): Bearer-token auth (SHA-256
  hashed, shown once), SQLAlchemy + SQLite (`create_all`, **no migrations**),
  users/connections(Fernet)/briefings/schedules, Stripe, OAuth (in-memory
  state), `/ui/*` read-only dashboard over `.ronin/` data, started via
  `ronin ui`. **No endpoint exposes the gated coding runtime.**
- `apps/web` (Next.js 15 App Router, Tailwind, TS): landing/signin/dashboard
  for the briefing SaaS. `"test": "echo 'frontend tests not wired'"`. Visible
  `csk` branding in `page.tsx`, `dashboard/page.tsx`, `signin/page.tsx`;
  localStorage key `csk.api_token`; API token prefix `csk_`.
- `apps/docs`: Mintlify. `apps/demo`: AgentLab pattern playground.
- `packages/relay`: phone→laptop tunnel, token-auth, no inbound port.

### Databases & migrations
Only real schema: 4 SQLAlchemy tables in `apps/api/csk_api/db.py`. No Alembic
anywhere. CLI state is JSON files under `.ronin/` / `~/.ronin/`.

### `csk` branding (user-facing)
`apps/web` logo/sign-in text (3 files), `server.py` FastAPI title,
`briefing_email.py` From-header, `csk_` token prefix, CLI docstrings, live
`csk` command alias. Internal-only: `csk_api` package dir, env names, legacy
dir migration.

## Preservation contract (what this program must not break)

1. Every CLI command name, including the `csk` alias.
2. `run_code_agent`'s signature and in-process safety wrapping.
3. The destructive floor + deny-rules + trust gates + fail-closed sandboxes.
4. Local-first/offline/free modes and keyless EmbeddedProvider.
5. The 3,642-test green baseline.
6. `.ronin/` on-disk formats (sessions, memory.json, settings.json, config.toml).
7. `training/` honesty invariants (no fabricated rows/metrics/evals).

## Migration map (evolve, don't rewrite)

| Target (Ronin AI OS) | Strategy |
|---|---|
| Ronin Core | The existing runtime IS the core. New surfaces call `run_code_agent`/`run_ask` through seams; no second intelligence implementation. |
| Industry Pack SDK | NEW `packages/industry-sdk` (this branch). Declarative manifests, fail-closed. |
| Worlds | `industry-packs/` tree (this branch): 3 initial worlds enabled, 17 future manifests validated-but-disabled. |
| Model/Adapter Registry | NEW modules layered over `PROVIDER_PRESETS` + `cost.py` (which remain source of truth for the CLI); adapters formalize what `$RONIN_ADAPTER` + training reports already do informally. |
| Vault | NEW scoped-memory layer; existing `memory_store.py` becomes the user-scope backend via adapter, untouched for CLI callers. |
| Forge | Extends `training/` + reuses `cli/finetune.py` generators; adds provenance, redaction review, bundle generation, job states. Never conflates generated with trained. |
| Ronin Code (web) | apps/api gains v1 endpoints wrapping `run_code_agent(gate_cb=...)`; approvals surface as pending records a human resolves. Web never bypasses terminal safety. |
| API v1 | Additive routers in apps/api; existing routes untouched. |
| apps/web | Rebrand csk→ronin, wire tests, add World Navigator; existing briefing pages remain. |
| Multi-agent diffs | orchestrate/dojo diffs surface as pending-review artifacts, never auto-applied. |

## Known risks accepted at baseline
- `config.py` ships a hardcoded dev `FERNET_KEY` default (flagged for the
  security pass).
- OAuth CSRF state store is in-memory (documented "swap to Redis in prod").
- No DB migrations; `create_all` only. Alembic remains deferred but the new
  v1 tables keep portable types (SQLite now, PostgreSQL-compatible).
