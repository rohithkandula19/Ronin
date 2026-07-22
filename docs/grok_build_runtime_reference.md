# Grok Build as a runtime reference for Ronin

**Scope guard, stated first:** [`xai-org/grok-build`](https://github.com/xai-org/grok-build)
is a coding-agent **runtime** — an Apache-2.0, ~99.6% Rust Cargo workspace (CLI/TUI/
harness) published 2026-07-14. It is **not a model**, it ships no weights, and nothing
in this document feeds the Qwen fine-tuning scorecard
(`training/reports/v3_finetune_comparison.md`). Ronin's fine-tuning stays on
`mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` until the v3b evidence phase closes.
This is architecture-reference material only.

Verified 2026-07-16 against the repo, its README, and its in-repo user guide
(`crates/codegen/xai-grok-pager/docs/user-guide/`). Point-in-time: ~5.6k stars,
issues disabled, external contributions not accepted. Claims we could **not**
verify (a "~1M lines" figure, a May-2026 beta, a SQLite session store mentioned
once against the better-documented JSONL layout) are not relied on below.

## Where the two runtimes stand, area by area

| area | Grok Build (verified) | Ronin today | verdict |
|---|---|---|---|
| Tool execution | `xai-grok-tools` crate: registry / implementations / schema split; `CanonicalToolMeta` envelope carries `name/kind/namespace/read_only` on every tool-call event | `Tool` = callable + JSON-schema + `sensitive` flag (`packages/agent-patterns/.../types.py`); registry via `build_code_tools`/`build_background_tools` (`packages/cli/src/ronin_cli/code_tools.py`, `bg_processes.py`) | parity on shape; Grok's explicit per-event tool identity metadata is worth borrowing for logs/evals |
| Approval pipeline | PreToolUse hooks → allow/ask/deny rules (deny>ask>allow severity) → remembered per-project grants → auto-approve read-only → mode prompt policy | deny-rules kill-switch (even under `--yolo`) → destructive floor → `SENSITIVE_TOOLS` gate (`code_mode._selective_gate`, `approvals.py`, `permissions.py`) | Ronin's floor is stronger; Grok's **remembered per-project grants** would cut prompt fatigue |
| Sandboxing | Linux **Landlock + seccomp network blocking + bubblewrap**; macOS Seatbelt; 5 named profiles + TOML custom; irreversible at runtime | `local` / `docker` / `ssh` / macOS `seatbelt`, fail-closed (`backends.py`); **Landlock appears only in an error string — not implemented** | **the clearest gap**: Ronin has no Linux OS-native backend; Grok Build proves the Landlock+seccomp shape |
| Hooks | ~14 lifecycle events (SessionStart … SessionEnd), command or HTTP handlers, only PreToolUse can block (exit 2 / `{"decision":"deny"}`) | 2 events (`post_edit`, `post_run`), trust-gated via content hash (`hooks.py`, `plugin_trust.py`) | Ronin's trust gate is ahead; the event *surface* is far behind |
| MCP | `~/.grok/config.toml` + project config, stdio/HTTP/SSE, OAuth, `server__tool` namespacing, `grok mcp` CLI | full bidirectional: stdio client, streamable-HTTP remote, ronin-as-server, catalog, `ronin mcp add/install/trust`; all MCP tools forced `sensitive=True` (readOnlyHint deliberately ignored) | near-parity; keep Ronin's distrust of `readOnlyHint` — it is the safer stance |
| Long-running tasks | `background:true` + task_ids, wait/kill tools, **Ctrl+G backgrounds a running command**, `/loop`, streaming monitor, scheduler API | `run_background`/`background_logs`/`background_status`/`stop_background` (gated, sandbox-aware), detached `ronin bg` workers | parity on primitives; missing: a blocking *wait* tool, promote-to-background, and any scheduler |
| Sessions / checkpoints | `~/.grok/sessions/` JSONL + `rewind_points.jsonl` file snapshots; `/resume`, `--resume/-c`, `/fork` (peer agent, optional **worktree**), `/rewind`, `/compact` | transcripts in `.ronin/sessions/`, structured `message_history`, checkpoint/rewind on `refs/ronin/checkpoints/*` + `ronin checkpoint`/`ronin undo` (`sessions.py`, `checkpoint.py`, `checkpoints.py`) | Ronin's git-ref checkpoints are arguably richer; missing: `/fork`-into-worktree and `/compact` |
| Headless | `grok -p` with plain/json/streaming-json output, `--max-turns`, `--tools/--disallowed-tools`, `--allow/--deny`, defined exit codes, **ACP** editor embedding | one-shot `ronin code "task"`, console=None callback runs, `csk serve` read-only API — no structured output mode, no defined exit-code contract | gap: headless is *possible* in Ronin but not *scriptable*; no JSON output, no ACP |
| TUI | full-screen, mouse-interactive, scrollback/prompts/modals (`xai-grok-pager`) | inline REPL + full-screen TUI (`tui.py`), plan/auto-accept modes, InputQueue | parity in spirit; no action needed |

## Ranked shortlist — what is actually worth borrowing

Ordered by (safety value × fit with Ronin's fail-closed design) / effort:

1. **Linux OS-native sandbox backend (Landlock + seccomp network deny).** Ronin's
   Phase-B containment shipped Seatbelt for macOS and fails closed elsewhere; the
   error string already promises Landlock. Grok Build demonstrates the exact
   composition (Landlock FS rules + seccomp to kill network + bubblewrap for deny
   lists). Slots directly into `backends.py` beside `seatbelt` with the same
   `wrap_command` contract and the same refuse-don't-degrade policy.
2. **Named sandbox profiles.** Five built-in profiles + user TOML in Grok Build vs
   Ronin's single implicit profile per backend. A `RONIN_BACKEND=seatbelt:strict`
   spec parse is a small change in `parse_backend` and makes containment auditable.
3. **Remembered per-project approval grants.** Grok persists "always allow X in
   this project" decisions between the rule check and the prompt. Ronin already has
   the trust-store pattern (`plugin_trust.py` content hashes) to build this on —
   same fail-closed storage, applied to approval decisions. Keep the destructive
   floor un-rememberable.
4. **Structured headless mode.** `-p`-style one-shot with `--output json|stream-json`,
   `--max-turns`, and a defined exit-code contract on top of the existing
   console=None path. This is also the piece that would let future *runtime-level*
   protocol evals drive Ronin end-to-end instead of scoring providers in isolation
   — the eval harness stays deterministic, the harness just gets a real subject.
5. **Wider hook lifecycle.** Ronin's two post-events → add SessionStart/Stop,
   PreToolUse (blocking, with the same trust gate), PostToolUseFailure. Grok's
   "only PreToolUse blocks" rule is a good simplicity constraint to copy verbatim.
6. **`/fork` into a git worktree + `/compact`.** Both compose with machinery Ronin
   already owns (git-ref checkpoints; structured message_history for compaction).
7. **Per-event tool identity metadata** (`kind`/`namespace`/`read_only` stamped on
   every tool-call trace, à la `CanonicalToolMeta`) — cheap in `types.py`, makes
   transcripts and eval extraction more mechanical. Do **not** adopt its cousin
   (auto-approving read-only MCP tools): Ronin ignores `readOnlyHint` on purpose.

Explicit non-goals from this reference: Rust rewrite (no), adopting ACP before
there is editor demand (defer), plugin marketplace (Ronin's catalog + trust model
already covers the need at current scale), scheduler API (revisit if `ronin bg`
proves insufficient).

## Sequencing

Nothing above starts until the v3b evidence phase is closed and its verdict is
committed (`training/reports/v3_evidence_status.md` holds the decision rule).
Items 1–2 are the natural next security PR after that; items 3–7 are incremental
and independent. If any item lands, it gets its own eval/verification story in
its own PR — none of it retroactively touches the Qwen checkpoint scores.
