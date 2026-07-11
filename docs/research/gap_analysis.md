## Ronin Gap Analysis — Honest Read

**Framing rule:** No head-to-head benchmark exists between Ronin and any tool. Every "Ronin leads" item below is **feature-present-and-plausible** based on Ronin's stated design (MIT license, 7-package Python uv workspace, terminal-native TUI, provider-agnostic + free-first + local/BYO routing, built-in fail-closed approval/permission gate). None is measured-to-beat. Adoption/benchmark numbers for competitors are vendor/registry/third-party-reported.

### Where Ronin genuinely leads (feature-present, honest)

1. **Uniform MIT + fully open, forkable core.** Cleanly ahead of the two proprietary flagships (**Claude Code**, **Cursor**) which cannot be forked/self-hosted/run offline, ahead of **Cline** (closed JetBrains client + hosted provider) and marginally ahead of the Apache-2.0 cohort (Codex, Aider, Continue, Goose, Gemini CLI, Roo Code) on patent/NOTICE simplicity. Note **OpenHands** and **OpenCode** are also MIT (OpenHands has a source-available enterprise/ carve-out; OpenCode has no carve-out) — so "MIT" alone is parity with them.

2. **Provider-agnostic + free-first + full-loop local/BYO as the default posture.** The sharpest structural contrast with Claude Code (Anthropic-only, no free tier), Gemini CLI (Gemini-only, free/consumer path being cut June 2026), and Cursor (cloud-tied, Tab/Composer not local). Gemini CLI's own deprecation is the concrete proof point. (Against Aider/Cline/Goose/OpenHands/OpenCode/Continue/Roo this is parity, not a lead.)

3. **Terminal-native as the *primary* interface, with a built-in approval/permission gate.** A real lead over **Cursor** (CLI is an explicit complement to a GUI-first IDE), **Roo Code** (VS-Code-bound, no headless/CI, and EOL), and **Aider** (coarse auto-apply+auto-commit, no fine-grained gate). Also a lead over the **OSS frameworks**, which ship no default file/shell gate at all.

4. **Live, single, actively-maintained codebase with no cloud/CI control-plane dependency in the core loop.** A continuity lead over **Continue** (OSS coding-agent monorepo read-only, pivot to hosted CI) and **Roo Code** (archived/EOL 2026-05-15). Ronin's core agent loop also needs no mandatory Docker runtime, unlike **OpenHands**.

5. **Python uv workspace hackability for a Python audience.** Plausibly easier end-to-end to fork/self-host than the Rust monoliths (**Codex**, **Goose**) or the split TS-server+Go-TUI build (**OpenCode**). This is a taste/audience argument, not a capability gap in the competitor.

### Where competitors genuinely lead (Ronin does not claim these)

- **Frontier first-party models + published (vendor-reported) benchmarks:** Claude Code (Claude Opus/Sonnet), Codex (GPT-5.x-Codex), Gemini CLI (Gemini 3 Pro), Cursor (Composer). Ronin ships no model and no benchmark.
- **Ecosystem scale / adoption:** OpenCode (~180k stars, vendor), Codex (~97k), OpenHands (~80k), Cline (~61k, 5M+ installs), Aider (~47k), Continue (~35k), Goose (~27k). Ronin's adoption is unknown.
- **Multi-surface reach:** IDE + web + mobile + SDK (Cline, OpenCode, Cursor, Continue, Goose desktop GUI, OpenHands Agent Canvas). Ronin is terminal-only.
- **Built-in MCP marketplace / one-click install:** Cline, Continue Hub, Cursor Customize, Goose 70+ extensions, OpenCode remote-MCP w/ auto-OAuth+DCR. Ronin's MCP marketplace maturity is unstated.
- **OS-kernel-enforced sandboxing:** Codex (Seatbelt/Landlock+seccomp), Gemini CLI (Docker/Podman/Seatbelt), OpenHands (Docker). Ronin's containment model is app-level/unstated.
- **Semantic codebase indexing / embeddings:** Cursor (272k context), Roo Code (Qdrant), Continue (@codebase), Aider (tree-sitter repo map). Ronin's repo understanding depth is unstated.
- **Verification/self-correction primitives:** Continue's markdown CI-check agents, Aider's auto-lint/auto-test repair loop, Cursor's native browser verify, OpenHands' sandbox test-runner + deterministic replay. Ronin claims no built-in verify harness in this dataset.
- **Advanced multi-agent orchestration:** Cursor best-of-N up-to-8 agents, OpenHands Agent Canvas (drives other agents via ACP), Cline parallel-agents+Kanban, Goose native subagents/Recipes, Roo Orchestrator/Boomerang, LangGraph/CrewAI primitives.

### Biggest gaps, ranked (most consequential first)

1. **No published quality/verification signal.** Every serious competitor ships either a vendor benchmark or a built-in verify loop (or both). Ronin has neither in this dataset — the single biggest credibility gap and the easiest to weaponize against it ("prove it works").
2. **No MCP marketplace / integration breadth.** MCP is now the near-universal tool standard; Cline/Continue/Goose/OpenCode inherit thousands of integrations. Ronin being "an MCP client" is table stakes; the *breadth* gap is real.
3. **Terminal-only, single-surface.** No IDE/web/mobile/SDK reach. Limits the addressable audience versus every multi-surface competitor.
4. **No sandboxing story.** Codex/Gemini/OpenHands enforce OS- or container-level isolation; Ronin's containment is unstated, which matters for the "autonomous within rules" positioning.
5. **No semantic codebase index.** Repo-scale understanding (embeddings/repo-map) is standard in Cursor/Roo/Continue/Aider; unstated for Ronin.
6. **Unknown adoption + no first-party model.** Structural, hard to close quickly; better neutralized by leaning into openness/portability than by chasing star counts.
