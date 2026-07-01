# Product Hunt copy

> Drafts. No fabricated metrics, stars, or scores.

## Name
ronin

## Tagline (≤60 chars)
The free, terminal-native AI coding agent you can trust

## Description
ronin is an open-source (MIT), Claude-Code-style AI coding agent for your terminal — but **provider-agnostic and free-first**. Run it for $0 on Gemini, Groq, Cerebras, OpenRouter, or Ollama, or plug in Claude/OpenAI. Every edit and shell command is gated behind a diff preview and your approval; read-only roles are enforced; a full offline mode keeps everything on your machine. Its opt-in verification pipeline (architect → implementer → reviewer → tester → verifier) reasons about your real diff, runs your test suites, and only commits after a passing verdict — with your y/N.

## First comment (maker)
Hi PH 👋 I built ronin because I wanted a Claude-Code-style agent that (a) runs free on open models, and (b) I could actually trust with write access. So everything routes through an approval gate, read-only roles can't edit, offline mode strips network tools, and there's a gated, evidence-based verification pipeline before any commit. It's MIT, backed by 3,274 offline tests. Honest status: not on PyPI yet (curl installer / `git clone + uv sync`), and I publish no SWE-bench score because I haven't measured one. Would love feedback on the safety model!

## Topics
Developer Tools · Artificial Intelligence · Open Source · Terminal
