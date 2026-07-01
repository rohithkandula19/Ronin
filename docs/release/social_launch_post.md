# Social launch copy (X / LinkedIn / Mastodon)

> Draft copy for the maintainer. No fabricated stars/users/scores — fill real numbers at post time. Do not add benchmark badges that weren't measured.

## X / Mastodon (short)

🐼 ronin — a masterless, terminal-native AI coding agent (Claude-Code-style) that runs **free**.

- Provider-agnostic: Gemini · Groq · Cerebras · OpenRouter · Ollama — or Claude/OpenAI
- Every edit & shell command is **gated** (diff preview + your approval)
- A verification **pipeline**: architect → implementer → reviewer → tester → verifier, with real-diff evidence and gated commit/PR
- Fully **offline** mode (zero egress) · 31-game arcade for breaks
- MIT · 3,274 offline tests

`curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash`

## LinkedIn (longer)

I've been building **ronin** — an open-source, terminal-native AI coding agent in the shape of Claude Code, but **provider-agnostic and free-first**. Point it at a free model (Gemini / Groq / Cerebras / OpenRouter / Ollama) and it codes for $0; plug in Claude for top quality. Same agent, same UI, your choice of brain.

What I care about most is **trust**:
- Every file write and shell command is gated behind a diff preview and your approval. Read-only roles are *enforced*.
- There's a sequential, gated verification **pipeline** — architect → implementer → reviewer → tester → verifier — that reasons about the **actual diff**, runs your test suites (required vs optional), and produces a Final Verification "truth table". A commit/PR only happens after a passing verdict, and only with your y/N.
- A true **offline** mode strips every network tool — nothing leaves the machine.

It's MIT-licensed, backed by 3,274 offline tests, and built as a reference for how to build agents responsibly.

Install: `curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash`

(Honest status: not on PyPI yet; SWE-bench harness ships but no score is published — I don't post numbers I haven't measured.)
