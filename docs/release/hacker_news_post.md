# Hacker News — Show HN draft

> Title and body drafts. HN rewards candor: keep the honest-status section. No fabricated benchmarks or metrics.

## Title options

- `Show HN: Ronin – a free, provider-agnostic terminal coding agent (Claude-Code-style)`
- `Show HN: Ronin – a terminal AI coding agent with a gated, evidence-based verification pipeline`

## Body

Ronin is an open-source (MIT), terminal-native AI coding agent shaped like Claude Code, but **provider-agnostic and free-first**. It reads, edits, and runs your code from the terminal; point it at a free model (Gemini / Groq / Cerebras / OpenRouter / Ollama) and it works for $0, or plug in Claude/OpenAI for top quality.

Two things I focused on:

**1. Safety by construction.** Every file write and shell command is gated behind a diff preview and an explicit y/N. Payments/destructive ops are hard-blocked (never auto-approvable). Read-only "roles" (researcher/reviewer/architect/verifier) are *enforced* — the agent is filtered to read-only tools, not merely asked to behave. A real `--offline` mode strips every network tool.

**2. Evidence-based verification.** There's an opt-in `ronin pipeline` that runs roles in sequence — architect → implementer → reviewer → tester → verifier — passing typed artifacts between stages. The verifier reasons about the **actual unified diff** (including new untracked files), runs your test suites (required vs optional, all gated), cross-checks an artifact contract, and emits a "Final Verification" truth table. A commit/PR only happens after a passing verdict and your approval — it's sequential and gated, not autonomous.

It's built as seven small, independently-usable packages (agent patterns, evals, memory, hardening, MCP), backed by 3,274 offline tests (a FakeProvider makes them deterministic and free in CI).

**Honest status:** it's v0.59 / release-candidate quality, not yet on PyPI (install is a curl script or `git clone + uv sync`). It ships a SWE-bench *harness* but I publish **no** SWE-bench score — I haven't measured one, so I won't claim one.

Install: `curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash`
Repo: https://github.com/rohithkandula19/Ronin

Feedback very welcome — especially on the safety model and the verification pipeline.
