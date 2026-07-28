# Ronin Contribution Roadmap

This roadmap translates the vNext architecture program into contributor-sized workstreams. It is intentionally not a promise that every item is already shipped. Treat it as a map for issues, design discussions, and focused pull requests.

For the detailed subsystem design, read:

- `docs/RONIN_MASTER_PROGRAM_vNEXT.md`
- `docs/architecture/ronin_vnext_architecture.md`
- `docs/architecture/module_boundaries.md`
- `docs/research/opportunity_report.md`

## Contribution Areas

| Area | Why it matters | Example deliverables |
|---|---|---|
| Core agent architecture | The runtime should plan, act, verify, and resume without losing the safety floor. | Planning engine improvements, execution engine seams, durable run journals, budget enforcement. |
| Multi-agent orchestration | Larger tasks need role separation and independent review. First slice shipped: a 1,170-profile generated specialist catalog, safe project manifests, task ranking, bounded active teams, provider routing, and isolated write tiers. | Architect -> implementer -> reviewer -> tester -> verifier flows, bounded repair loops, role-specific artifacts. |
| Context and memory | Ronin should remember useful project facts without storing secrets or bypassing approvals. | Long-term memory, local semantic search, retrieval tuning, memory migration tools. |
| Coding engine | Patches should be precise, reviewable, and validated against the repository shape. | Smarter diffs, AST-aware editing checks, patch verification, changed-file impact analysis. |
| Safety | Autonomous work only earns trust when dangerous actions remain gated everywhere. | Destructive command protection, tool sandboxing, approval policy tests, browser/MCP/plugin floor coverage. |
| Provider ecosystem | Users should be able to bring the model stack they already trust. | OpenAI, Anthropic, Gemini, Ollama, OpenRouter, local runtime, provider health and capability metadata. |
| Evaluation | Ronin needs measured evidence, not vibes, for regressions and releases. | SWE-bench, HumanEval, internal regression suites, statistical reporting, benchmark history. |
| CLI and UX | Terminal-native workflows should be clear, resumable, and low-noise. | Streaming diff renderer, progress UI, approvals, resumable sessions, TUI parity. |
| Release engineering | A reliable tool needs reproducible packaging and conservative release gates. | CI hardening, installers, clean-install checks, reproducible builds, release automation. |
| Documentation | Contributors and users need accurate, grounded guides that match the code. | Architecture docs, tutorials, examples, contributor guides, migration notes. |

## Realistic PR Tracks

Each track should be small enough to review, but substantial enough to include code, tests, docs, and a changelog entry when user-facing. Add migration notes when persisted state, config, CLI flags, or public APIs change.

| Track | Code | Tests | Docs and release notes |
|---|---|---|---|
| Memory compaction and retrieval improvements | Add persistent retrieval hooks and tune compaction boundaries. | Unit tests for recall ranking, expiry, secret non-storage, and fallback behavior. | Memory docs, limitations, migration note for stored formats. First slice shipped: dependency-free `SqliteBackend` for persistent local recall. |
| Agent planning cache | Cache reusable plan fragments by repo/task fingerprint under `.ronin`. First slice shipped: opt-in atomic `PlanCache`, structured hit/miss trace metadata, repository invalidation, and bounded retention. | Cache hit/miss tests, invalidation tests, budget tests. | [Architecture note](architecture/planner_cache.md), agent-patterns docs, and changelog. |
| Streaming diff renderer | Render incremental unified diffs with stable widths and no markup injection. First slice shipped: reusable chunk-aware renderer, fixed-width Rich `Text` rows, safe raw fallback, and `NO_COLOR` coverage. | Snapshot/pure render tests, narrow terminal tests, `NO_COLOR` tests. | CLI UX docs and changelog. |
| Better checkpoint recovery | Make agent-facing restores reversible and expose dry-run restore plans. First slice shipped: `preview_rewind`, mandatory pre-restore safety snapshots, preserved checkpoint metadata, and atomic index writes. | Restore-plan tests, dirty-tree tests, metadata atomicity tests. | Safety docs, rollback notes, and changelog. |
| Provider auto-discovery | Detect configured provider keys/endpoints and report capability/cost status honestly. First slice shipped: provider-specific environment-key discovery, truthful key-source reporting, and model discovery that uses the selected provider's credentials. | Provider matrix tests with fake env/config, unknown-price tests. | Provider docs and changelog. |
| Plugin/tool SDK | Add manifest parsing, capability metadata, trust gating, and scaffolding. First slice shipped: literal AST-read manifests, host-owned capability metadata, fail-closed legacy defaults, and explicit high-risk approvals. | Non-exec manifest tests, trust-store tests, sensitive-tool regression tests. | [Plugin SDK guide](plugins.md), security note, and changelog. |
| Token budgeting improvements | Track per-run token/wall-clock/cost budgets and stop cleanly. First slice shipped: `ReActAgent` ceilings retain partial output, skip newly requested tools, and never guess provider costs. | Budget exceeded tests, partial-result tests, no-overclaim tests. | Agent-patterns docs and changelog. |
| AST-based patch verification | Verify edits preserve parseability and public API expectations where possible. First slice shipped: pre-write Python AST parsing, all-or-nothing edit rejection, public-symbol removal warnings, and optional local TypeScript parsing. | Python/TypeScript fixture tests, failure reporting tests. | [Coding-engine verification](coding_engine.md), known limits, and changelog. |
| Performance benchmarking framework | Add repeatable startup, repo scan, routing, and eval timing reports. First slice shipped: deterministic executor injection, versioned JSON reports, atomic persistence, and failure-aware median regression comparison. | Deterministic fake benchmarks, report schema tests. | [Performance benchmarks](performance.md) and changelog. |
| Release automation improvements | Harden version bump, build, clean-install, checksum, and release-note flow. First slice shipped: fixed release manifest validation, synchronized package/pin updates, artifact-set checks, checksums, clean-install gating, and GitHub Release uploads. | Tag/version fixture tests, dependency-drift tests, artifact checks. | [Release automation](release_automation.md), migration note, rollback plan, and changelog. |

## Issue Backlog Seeds

Use these as issue titles or tracking epics. Link each issue to one roadmap area and add acceptance criteria before implementation starts.

- Improve context compression.
- Add semantic memory.
- Parallel planner execution.
- Tool sandbox improvements.
- Better Docker support.
- Native Windows support.
- Local model benchmarking.
- Provider health monitoring.
- Multi-worktree execution.
- Agent telemetry dashboard.

## Pull Request Bar

Every non-trivial PR should answer:

- Summary: what changed, in one paragraph.
- Motivation: why the change matters now.
- Design: the main approach, alternatives considered, and any boundary decisions.
- Tests: exact commands run and notable skipped/not-run checks.
- Documentation: docs, help text, examples, and changelog updates.
- Risks: safety, compatibility, performance, and migration risk.
- Rollback plan: how to revert or disable the change.
- Checklist: docs, tests, changelog, no secrets, no safety-floor regression.

## Commit Style

Use scoped, conventional-style commit messages in imperative voice:

```text
feat(agent): add planner cache
feat(memory): add semantic retrieval
fix(cli): improve progress rendering
test(eval): add regression suite
docs: update architecture guide
refactor(runtime): simplify execution pipeline
perf(memory): reduce token usage
ci: improve release workflow
```

Prefer one logical change per commit. If a PR needs several commits, keep each one reviewable on its own.

## Long-Term Vision

Ronin should grow beyond "another coding assistant" into an autonomous engineering platform that remains local-first, provider-agnostic, safety-first, and honest about evidence.

Long-range capabilities:

- Multi-agent collaboration.
- Long-term project memory.
- Repository understanding.
- Autonomous planning.
- Safe code execution.
- Self-evaluation.
- Checkpoint and resume.
- Local and cloud execution.
- Extensible tools and plugins.
- Human approval workflows.

## Suggested Milestones

| Milestone | Theme | Success shape |
|---|---|---|
| v1.0 | Stable coding agent | Installable, honest, safety-gated, documented baseline. |
| v1.5 | Advanced pipelines and memory | Durable runs, stronger retrieval, better verification loops. |
| v2.0 | Team-scale multi-agent system | Role pipelines, review/test/verifier discipline, shared project memory. |
| v3.0 | Autonomous software engineering platform | Safe longer-running execution, richer tool/plugin ecosystem, benchmarked quality. |
| v4.0+ | Research-oriented adaptive agent platform | Adaptive planning, deeper evals, reproducible agent experiments. |

## How Contributors Can Help

Helpful contributions include architecture documents, agent frameworks, planning systems, memory systems, evaluation harnesses, CLI improvements, provider integrations, release automation, CI/CD, packaging, benchmarks, documentation, PRs, tests, and release notes.

Start with an issue for broad or cross-cutting work. For narrow fixes, open a focused PR with tests and a clear rollback plan.
