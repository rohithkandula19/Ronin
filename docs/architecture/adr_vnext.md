# Ronin vNext — Architecture Decision Records

One ADR per subsystem. Format: **Context / Decision / Consequences / Alternatives**. The through-line: *evolve behind existing seams, preserve both floors, stay local-first.*

---

## ADR-1 — Agent runtime: evolve the ReAct loop, do not rewrite

**Context.** The runtime is genuinely good and heavily tested (123 offline tests); the weaknesses are operational, not architectural. Long autonomous runs are non-resumable (in-memory history), "success" means the model stopped calling tools rather than that the change was verified, there is no unified budget ceiling, and the destructive floor lives in two partly-overlapping gate implementations. The temptation is a rewrite into a durable-workflow engine; that would discard a working, offline-testable loop.

**Decision.** Add four capabilities that all attach to the existing hook surface (`on_step`/`before_tool`/`after_tool`/`messages`) so `react.py` internals are literally unchanged: (1) an optional `RunJournal` for crash-resume + audit; (2) a pluggable `Verifier` stage (repo-declared verify command → one bounded reflexion retry) unifying today's swarm-reviewer + `EditGuard` + new behavioral verify; (3) a single `RunBudget` checked at the existing usage-accumulation site; (4) consolidation of the destructive floor into one shared authority both gates call, plus a `sub_agent_depth` cap. Every new param defaults to None/off.

**Consequences.** Crash-resumable long runs; honest success (verified, not just "stopped"); real token/$/time ceilings; one floor authority with an invariant test; bounded recursion. Testability stays high (protocols + emit-sinks, offline via `FakeProvider`). Costs: two more optional params; verifier latency on interactive runs (off by default); a capped, gitignored JSONL journal. Risk: consolidating the two gates could regress the floor — mitigated by extracting only the already-shared predicate and adding the invariant test *before* touching either caller.

**Alternatives.** (a) Adopt a durable-workflow engine (Temporal/DBOS) — rejected: heavyweight, network/daemon, breaks local-first and offline testing. (b) Rewrite the loop as an explicit state machine — rejected: compaction/parallel/streaming are subtle and well-tested; a journal delivers resumability additively. (c) Leave verification to the human gate only — rejected: the gate approves an *action*, it does not confirm the change *works*. (d) Fold everything into `OrchestratorAgent` — rejected: that's for multi-specialist decomposition, not single-thread code runs.

---

## ADR-2 — Provider router: one registry, tri-state pricing, one selection core

**Context.** Pricing/free-tier truth is duplicated across `cost.py`/`bench.py`/`offline.py`/`slash_commands.py` and has drifted (anthropic $6 vs $9; keyless `local` mislabelled PAID; paid models on free-tier providers badged FREE and costed $0). Two overlapping routers share stats/cost but not a selection core. Auto-failover is Cerebras-only.

**Decision.** Consolidate all provider metadata into one `providers_registry.py` that re-backs `price_for`/`is_free`/`cost_badge` **without changing signatures**; make pricing **tri-state** (`free|paid|unknown`) end-to-end so nothing under-reports; route both the `ronin route` command and the interactive loop through the existing pure `pick_blade` behind one `select_blade`; generalize the same-key sibling failover map. Pure cores, `RouterStats`, `CostLedger`, and `FailoverProvider` semantics are preserved.

**Consequences.** +1 small module, −4 drifting definitions. Free-badge and savings become accurate (`local` free; paid-on-free-provider no longer silent $0). Low migration risk (`is_free` stays boolean via `status=='free'`; `pricing_status` is additive). A drift-regression test locks the single source of truth. The interactive loop gains a soft budget signal (no behavior change when unset).

**Alternatives.** (a) Leave as-is — drift and over-reported free-turns keep growing; rejected. (b) A rules-engine/YAML pricing config with live catalogue sync — overkill, adds a network dependency, undercuts $0/offline-first; rejected. (c) Merge the two routers into one command — loses the read-only-ask vs interactive-turn distinction; rejected.

---

## ADR-3 — Local runtime: one pure selection seam, wire the built pieces, fix the three bugs

**Context.** Three local backends (embedded mlx/llama-cpp, Ollama, generic localhost) but selection policy is scattered and buggy: `is_local_provider` doesn't recognize the embedded brain so `--offline` downgrades the most-local backend (confirmed); `doctor --check` mis-reports the embedded provider as keyless-missing; the purpose-built fully-offline `local_embed.py` is orphaned (zero callers), so embedded users get no semantic search.

**Decision.** Consolidate local-brain selection behind one pure total policy seam (`resolve_local_backend`) and **wire the already-built pieces** (`local_embed` fallback, embedded verify/health) rather than rewrite any engine. Fix the three bugs surgically. Keep the gate and floor strictly downstream and untouched — the runtime only chooses a brain and *strips* (never adds) tools.

**Consequences.** Offline becomes trustworthy (never downgrades or egresses; embedded is a first-class offline citizen); embedded users gain honest health + in-process semantic search; policy is pure and unit-testable; working engines/tests preserved. Costs: small additive surface in `get_backend`/`_provider_live_check` + one policy fn; the embedded tool-calling ceiling remains (documented, deferred); a few new tests.

**Alternatives.** (a) Full rewrite into a `LocalRuntime` service class — rejected as a gratuitous rewrite that breaks working engines/tests. (b) Prompt-parser shim so embedded models emit structured tool calls — deferred to its own sprint (high-risk, orthogonal). (c) Drop `local_embed` and mandate Ollama for all offline embeddings — rejected: breaks the air-gapped/no-daemon promise `local_embed` was built to keep.

---

## ADR-4 — Memory: implement the existing Protocol locally, converge the drift

**Context.** The kit is clean and tested, but long-term "vector store" is a Protocol with one naive in-memory Jaccard backend and no persistence; the promised `ChromaDBBackend` does not exist. The running agent bypasses the kit with a separate JSON+Jaccard store whose docstring falsely claims it uses the kit. The repo already ships local-first embeddings.

**Decision.** Do **not** adopt chromadb/Pinecone. Implement `LongTermBackend` with (a) a stdlib-sqlite persistent backend as the new default and (b) an embedding-aware query reusing the in-repo local embeddings via an injected `embed_fn`, **failing open** to the current Jaccard rank. Rebuild `cli/memory_store.py` on top of `LongTermMemory` so there is exactly one long-term implementation. Keep every public class/method signature; short-term and preferences change only additively.

**Consequences.** The "pluggable vector store" promise is met locally with zero new heavy deps and zero egress; the two divergent stores collapse into one tested abstraction; recall gains semantic ranking + durable persistence; preferences stop being dead code. Costs: a one-time `memory.json`→sqlite migration; a small versioned on-disk schema; embedding recall is only as good as the hashing-trick vectors until Ollama is configured. Memory stays strictly advisory — no gate/floor change.

**Alternatives.** (1) Ship `ChromaDBBackend` as advertised — rejected: heavy dep, a daemon, network surface, duplicates existing local embeddings, contradicts local-first. (2) Full rewrite into one unified memory service — rejected: the kit is tested and published; the drift is fixable behind the existing Protocol. (3) Just delete the chromadb promise, keep the Jaccard dict — rejected: it is the actual functional gap. (4) Hosted embeddings requirement — rejected: breaks $0/offline.

---

## ADR-5 — Repo intelligence: consolidate on the SQLite index, add a composed risk signal

**Context.** Three good-but-siloed tools. BM25+tokenizer weighting is duplicated across `repo_map.search` and `repo_index.rank_files`; `radius.py` re-walks/re-parses the whole tree every call and is Python-only in a polyglot TS monorepo (JS/TS diffs report empty blast radius); "risk" is only per-file structural smell (no churn/hotspot, no centrality coupling); the persistent index stores symbols but not import edges, so radius and the index can't share the walk.

**Decision.** Make `.ronin/index.db` the single substrate: add an import-edges table populated in `build_index`'s existing incremental walk, and have radius read edges from it (in-memory builder kept as fallback + test seam). Keep every pure/tested core unchanged. Add one new pure `risk.py` composing smell + `git log --numstat` churn + import-graph fan-in into a ranked hotspot score. Make impact per-language pluggable behind a thin `ImportExtractor` protocol (Python `ast` today; JS/TS regex added). Everything stays read-only and offline.

**Consequences.** Radius/map become incremental and stop double-walking; a real risk/hotspot signal ships with zero new deps, offline; polyglot impact is additive/opt-in; one tokenizer/ranker to maintain. Caveats: an index schema migration (guarded by `schema_version` + migrate-by-rebuild); heuristic JS/TS extraction is less precise than Python's `ast` (documented, LSP-upgradeable); more read-only surface to keep safe.

**Alternatives.** (1) Full tree-sitter/LSP graph for all languages — rejected: heavy dep, breaks "runs anywhere, no model, no network," rewrites a working `ast` path. (2) Bolt on a standalone risk command — rejected: perpetuates the double-walk + BM25 duplication. (3) Embeddings-only retrieval — rejected: already optional, not the bottleneck, not offline-free. (4) Cache the in-memory graph in `repo_map._CACHE` — rejected: doesn't survive sessions, still two walks.

---

## ADR-6 — Checkpoint: one shared core, make the agent rewind reversible

**Context.** Two parallel engines with divergent safety. The agent-facing rewind (int ids, `refs/ronin/checkpoints`) is **irreversible** and, because `rewind` is SENSITIVE-but-not-a-shell-command, is auto-approved under `--yolo` and never hits the destructive floor — a yolo agent can silently, unrecoverably rewind. The user-facing engine (base-36 ids, `refs/ronin/session`) **is** reversible and has the tested pure core + fallback. Both share correct git plumbing but both have non-atomic index writes (crash → orphaned refs), no retention (unbounded refs), and a parallel-agent write race — all under a stated fleet-of-agents direction.

**Decision.** Consolidate onto one shared core extracted from `checkpoints.py`'s proven internals, with both surfaces as thin adapters. **Promote reversibility to the agent path:** rewind always takes a pre-restore safety snapshot and returns its id. Make index writes atomic (temp + `os.replace`), add a file lock around id-allocation/save, add an opt-in (never-auto) prune, and expose `restore_plan` as a dry-run. Keep both id namespaces via a thin adapter instead of a risky migration.

**Consequences.** The auto-approvable agent rewind becomes recoverable even under god-mode, strengthening safety while leaving the floor and gate exactly as they are. No more silent index loss; bounded ref growth; lossless parallel checkpointing; agents can preview blast radius. Costs: a small id adapter, one extra cheap non-destructive snapshot per restore, slightly more shared-core code offset by shrinking the two surfaces. Git-plumbing behavior (covered by the suite) is unchanged.

**Alternatives.** (a) Hard-merge to one id scheme now — rejected: breaks stored int `checkpoint_id`s in `auto.py`/`pipeline`/saved `--resume` states; too risky for v1.0.0. (b) Leave as-is — rejected: leaves the irreversible-yolo-rewind gap and the silent-loss path. (c) Route rewind through the typed-phrase floor — rejected as a *false* floor: once rewind snapshots first it is locally reversible, so a mandatory phrase every time is approval fatigue without added safety; the floor stays reserved for genuinely irreversible shell actions.

---

## ADR-7 — Pipeline: harden the verifier, add one gated repair hop

**Context.** Feature-complete and heavily tested at v1.0.0, but with four maintenance hazards and one UX gap: artifact JSON shapes hand-duplicated between `_ARTIFACT_HINT` and the pydantic models (no derivation/`schema_version`, silent drift); verdict precedence re-encoded in three functions across two files; verify-source selection a dense inline ladder; the run is strictly linear and hard-halts on any block; observability is render-only.

**Decision.** Evolutionary hardening: (A) derive artifact prompt-shapes + a `schema_version` from the pydantic models as the single source of truth while keeping the lenient parse; (B) consolidate verdict precedence into one pure `VerdictEngine`; (C) extract a pure `select_suite_specs`; (D) add a local append-only JSONL event log as a pure injectable sink; (E) add an opt-in (`--repair-rounds`, default 0), hard-capped, fully-gated single-hop repair loop that reuses the same `yolo=False` stage runner and adds no new gate bypass.

**Consequences.** One place to change an artifact contract; an exhaustively table-testable verdict; debuggable headless runs + a learning-loop feed; optional recovery from a fixable block without a human re-invoke; a leaner core. Costs: a new `schema_version` to bump; the repair loop is a code path that must stay gated + bounded (mitigated: default-off, hard-capped, reuses `run_code_agent(yolo=False)`, and the `VerdictEngine` still cannot emit unknown/blocked→passed). Refactors A/B/C are guarded by the ~2,300 LOC test suite.

**Alternatives.** (1) Full rewrite into a generic DAG executor — rejected as gratuitous: discards a clean pure/impure design and green tests for no user-visible gain. (2) Unbounded autonomous multi-round self-correction — rejected: violates the "sequential, not autonomous" contract and the approval-every-move rule; unbounded loops burn budget and erode the gate. (3) Leave as-is — rejected: silent schema drift and triplicated verdict logic are real hazards, and a hard halt on a recoverable block is rough UX.

---

## ADR-8 — Plugin runtime: one hardened load path, a trust gate, capabilities, opt-in isolation

**Context.** Plugins execute arbitrary in-process Python discovered from project-local `.ronin/plugins/`. Three problems: (a) discovery exec's every file at session start, so a cloned/hostile repo's plugin runs top-level code **before any gate** — a load-time RCE the permission system defends allow-rules against but plugins do not; (b) the gate + floor only see the tool-call boundary, blind to what a handler does internally; (c) a duplicate-load + first-wins dedup keeps an un-hardened, `sensitive=False` copy, so plugin calls appear to bypass the gate entirely.

**Decision.** Evolve, not rewrite. (1) Collapse to a single hardened+sensitive load path so every plugin call is gated + error-isolated. (2) Add a `permissions.py`-style per-repo **trust gate** plus a non-executing AST/header **manifest** read, so untrusted plugins are listed but never auto-exec'd. (3) Add fail-closed **capability metadata** feeding the existing gate (payment/subprocess route to block). (4) Add **opt-in handler isolation** reusing `backends.py`. `approvals.py`'s gate and floor remain the unchanged outermost authority.

**Consequences.** Closes the load-time RCE and the gate-bypass; approvals become informative instead of opaque; optional real sandboxing exists without changing defaults. Cost: a one-time per-repo trust prompt (mirrors the shell allow/deny UX) and small changes concentrated in `plugins.py` + a thin trust module + one manifest field. No behavior change for already-trusted local plugins with sandbox unset.

**Alternatives.** (a) Full rewrite to subprocess-only plugins — rejected: breaks drop-in DX, gratuitous. (b) In-process RestrictedPython/AST sandbox — rejected: not a real boundary, high complexity, false confidence. (c) Drop native plugins, MCP-only — rejected: loses the `plugin from-api` one-command differentiator. (d) Do nothing — rejected: the load-time exec and dedup bypass are concrete.

---

## ADR-9 — MCP runtime: extend the destructive floor to MCP, concurrent+lazy startup

**Context.** The MCP runtime works and is secure-by-default, but (a) eager serial startup adds up to N×30s latency, (b) the destructive typed-confirmation floor is `run_command`-only, so under god-mode an opaque third-party MCP tool (`delete_repo`, refund, `DROP`) auto-runs with no floor — violating "god-mode never bypasses catastrophic protection," and (c) there is no per-server tool control, health, or stderr visibility.

**Decision.** Preserve the transport clients, `.ronin/mcp.json`, the all-tools-sensitive/never-trust-`readOnlyHint` posture, and the fail-closed drift guard. Add: (1) a pure `is_destructive_mcp_tool` classifier wired into **both** gate paths ahead of the yolo/allow-rule short-circuits, forcing typed confirmation on destructive MCP calls even under god-mode; (2) concurrent connect + an opt-in cache-backed `LazyMCPClient`; (3) optional per-server `timeout`/`tools`/`exclude`/`maxTools` via a pure filter; (4) light `is_alive()`/one-shot reconnect + a stderr ring buffer. No new dependency; no rewrite.

**Consequences.** Launch latency drops to ~one server's cost; god-mode can no longer silently run a destructive MCP tool; users can trim noisy servers; dead servers recover once and report honestly. Costs: the heuristic floor has false positives (safe direction — one extra confirm); the schema cache can go stale (verified on first real call, refreshed by `mcp list`); `build_mcp_tools` grows modestly. Existing tests keep passing.

**Alternatives.** (i) Adopt the official `mcp` SDK — rejected: heavy dep for a local-first CLI when ~370 tested lines suffice. (ii) Trust `readOnlyHint` to auto-exempt read tools — rejected on security grounds (a malicious server could lie). (iii) A warm cross-session daemon — rejected as overengineering; concurrent+lazy+cache captures most of the benefit. (iv) Classify destructiveness from server annotations — rejected: never trust the server.

---

## ADR-10 — Browser runtime: give browser actions the same catastrophic floor as shell

**Context.** The browser action runtime (navigate/click/type) is exposed to every code agent but gated only as a generic sensitive tool. `approvals.py` has a strong fail-closed payment/destructive BLOCK floor that yolo cannot bypass — but that floor is consulted **only for `run_command`**. Under `--god-mode` a `browse_click` on a "Confirm and pay" control auto-approves. The robust payment guards protect flows that never issue a live click; the flow that *can* click is the ungated one.

**Decision.** Add a pure `browser_intent.py` that translates each browser tool call into the existing approvals `Action` shape and reuses `gate_level` to detect a BLOCK-level browser action. Wire it as a browser-specific floor mirroring the `run_command` floor, enforced even under yolo, **before** the yolo short-circuit. Point `act.py`'s PREPARE stub at the now-gated primitives. Preserve the Playwright session, the 5 tools, and `approvals.py` unchanged.

**Consequences.** The browser runtime inherits the same catastrophic floor as shell; a pay/submit click can never auto-run under god-mode; the classifier is pure and trivially unit-tested; `act.py`'s `do` path gains real form-fill with no new unguarded surface; `approvals.py` stays the single policy source. Costs: one extra typed-confirmation on genuine payment clicks (by design); marker-based detection can miss a non-obvious pay button (mitigated: `reversible=False`/`external=True` default routes it to CONFIRM, never AUTO, so worst case is a prompt); wiring in two gate paths that must stay in sync (covered by a drift test).

**Alternatives.** (a) Do nothing — rejected: leaves the god-mode browser-payment bypass. (b) Make every browser tool always prompt regardless of yolo — rejected: breaks yolo for benign navigate/read and ignores the money/destruction distinction. (c) Push the guard into `browser_tools.py` (call approvals inside `browse_click`) — rejected: couples the runtime to the gate, bypassable by direct callers, duplicates the chokepoint. (d) Full rewrite to a multi-context/allowlisted browser platform — rejected as overengineering; those pieces are optional additive follow-ons.

---

## ADR-11 — Dashboard: split the app, make it structurally read-only, add liveness

**Context.** One FastAPI app conflates a read-only, offline, local-first dashboard (`ronin ui`) with a legacy multi-user `csk` SaaS (billing/oauth/signup). The dashboard works and is well-tested but (a) swallows backend load errors as an empty state, (b) is static (manual refresh, no live runs), (c) shares a process with billing/oauth endpoints it never needs, and (d) surfaces less than the `ronin status` terminal cockpit despite the data already existing on disk.

**Decision.** Evolve, not rewrite. (1) Split `main.py` into `dashboard_router` and `saas_router`; give `ronin ui` a dashboard-only app that is **GET-only by construction** (structural read-only, 405 on non-GET). (2) Fix the error/empty conflation and add a same-origin SSE `/ui/events` liveness ping. (3) Add `/ui/status` reusing the existing pure `ronin_cli.dashboard.gather()` for cost/leaderboard/router parity. Keep the self-contained HTML page, the read-only adapters, and the SaaS product intact.

**Consequences.** The local dashboard becomes smaller, auditable, and near-live; the read-only guarantee is enforced by the app instead of by convention; web reaches parity with the terminal cockpit with no new data code. Costs: a careful router refactor, a little SSE JS, and extending the offline/self-contained tests to cover `/ui/events` + `/ui/status` (both must stay relative-path). The gate and floor are untouched — the dashboard stays outside the gated path.

**Alternatives.** (1) Rewrite the page as the Next.js `apps/web` app for `ronin ui` — rejected: breaks the offline/self-contained guarantee and adds a build step to a localhost tool. (2) Add browser-initiated run/approve actions — rejected for vNext: creates a mutation path outside the CLI chokepoint; permissible only later if routed through `code_mode._approve`, never a direct executor. (3) Merge the SaaS and local dashboards into one auth'd product — rejected: violates local-first and couples a read-only tool to billing/oauth.

---

## ADR-12 — Terminal + TUI: one view-model, move the floor beneath the renderer boundary

**Context.** The presentation layer has a clean pure-builders + thin-render-shells split on the Rich/REPL side, but `tui.py` (the `ronin --tui` Textual app) shares **none** of the pure cores — it re-declares its own palette, tool labeler, streaming, and approval screen. `status_segments`' "single source of truth" claim is aspirational. The safety-critical consequence: the destructive floor is enforced only in the console path; the `gate_cb` path used by the TUI/headless has no floor check and `if yolo: return True` auto-approves any sensitive tool — so "god-mode never bypasses catastrophic protection" holds in the REPL but is **violated under `ronin --tui`.**

**Decision.** Consolidate the pure builders into a renderer-agnostic `view_model.py` that both backends consume (`tui.py` drops its private palette/labeler for the shared tokens), formalize a tiny structural `Renderer` protocol, and **relocate the destructive/payment floor into a shared, renderer-agnostic gate core (`approvals.enforce_floor`)** evaluated **before** any yolo/allow short-circuit on both `before_tool` paths, using a renderer-supplied confirmation callback. The renderer supplies the confirmation UI; the policy is renderer-independent. Keep `prompt_box`'s 3-tier fallback and `prompt_pinned` as-is.

**Consequences.** The two stacks become pixel-consistent from one source; ~3 divergent implementations are deleted; and — the load-bearing win — god-mode/yolo can no longer auto-run a catastrophic command in *any* renderer. Costs: a view-model extraction and a TUI adapter; the TUI approval flow must gain a typed-confirm modal. New tests: a TUI/headless destructive-floor test (currently missing) + parity tests that both backends render identical labels/status. Pinned safety chips extended to the TUI header.

**Alternatives.** (a) Leave the floor inside the console renderer — rejected: it is the concrete god-mode bypass under `--tui`. (b) Duplicate the floor check into `tui.py` — rejected: re-creates the drift the whole ADR removes; a shared core with a drift test is correct. (c) Rewrite both renderers on one framework — rejected: gratuitous; the backend-specific draw code (Rich Live vs Textual) is fine, only the text/label/summary/policy logic needs sharing.

---

## ADR-13 — Verification + benchmark: add statistical rigor, persistence, and gate the destructive evaluator

**Context.** The verify/eval/bench engines are functionally solid and honest about pass/fail, but every headline metric (judge mean, `resolved_rate`, bench pass-rate, drift delta) is a bare point estimate with no error bar; drift and `compare_swebench` fire regression gates on raw deltas, so noise on the small local datasets that are the norm is indistinguishable from signal. There is no run history, so baseline workflows are manual two-file juggling. And `make_local_git_evaluator` runs `git reset --hard` + `clean -fdx` **outside** Ronin's approval/destructive floor.

**Decision.** Evolve, do not rewrite. Add a pure, unit-tested `stats.py` (Wilson/bootstrap CIs + CI-aware `significant_regression`) exposed as **additive optional fields** on the existing report types; add opt-in `seed`+multi-sample (default `samples=1` = current behavior) so CIs have real inputs; add a local-first `evalstore.py` that mirrors `run_store.py` conventions for baseline history; and wrap the SWE-bench local evaluator so its destructive git ops pass through `is_destructive_command` + the existing default-deny confirm gate. Keep the suite/judge/harness/`verify_cmd`/`pipeline_verify` internals intact.

**Consequences.** Trustworthy numbers with honest error bars; regression gates that separate signal from noise; one-command "vs last green" drift; and the last floor-bypass in the benchmark surface closed. Every change is backward-compatible (new optional fields, `samples=1` default, raw threshold retained as a floor). Costs: opt-in multi-sample runs cost k× tokens/time; CIs on tiny datasets are honestly wide (a feature — it stops overclaiming); one more local dir under `RONIN_HOME`. Risk: the statistics must be correct — mitigated by keeping `stats.py` pure with known-value fixtures (Wilson bounds checked against reference tables).

**Alternatives.** (a) Full rewrite onto a third-party eval framework (inspect-ai / lm-eval-harness) — rejected: discards the working, honest, gate-integrated harness and the local-first/$0 posture, pulls in a heavy dependency. (b) Ship statistics but keep raw-delta gates — rejected: leaves the core credibility gap. (c) Persist to a DB — rejected: `run_store.py` already establishes pure-JSON/`RONIN_HOME` as the local-first precedent. (d) Consolidate the two `swebench.py` modules and three commands now — deferred: churny, orthogonal to the value, and risks the working harness.
