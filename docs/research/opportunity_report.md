## Ronin Opportunity Report — Open Lanes

Each lane is a differentiator Ronin's stated design can credibly own. Effort is a rough T-shirt estimate. No lane assumes or implies Ronin already beats a competitor — these are positioning + build opportunities. Ordered by leverage.

### 1. "Portability / no-lock-in" as the headline identity — LOW effort (positioning)
The clearest open lane in the whole field. Claude Code and Cursor are closed and paid; Gemini CLI is cutting its free/consumer path (June 18 2026) toward the closed Antigravity CLI; Codex has OpenAI gravity + a Responses-API-only local wire. **Why it matters:** vendor deprecation risk is now a live, dated event the market can feel. Ronin's MIT + provider-agnostic + free-first + local/BYO stack is the direct hedge. **Do:** make "runs $0, fully local, any model, no account, no egress, forkable" the top-line message, with the Gemini deprecation as the case study. No engineering required.

### 2. Ship a verification / self-correction loop — MEDIUM–HIGH effort
The biggest *capability* gap (see gap analysis #1). Continue (markdown CI-check agents), Aider (auto-lint/auto-test repair), Cursor (browser verify), OpenHands (sandbox test-runner + deterministic replay) all have one; Ronin does not. **Why it matters:** it is the answer to "prove it works," and it compounds with Ronin's existing gate/approval discipline. **Do:** add a run-tests-and-self-correct loop gated by the existing approval system; deterministic-first to fit the $0/local ethos. Lower-effort MVP: adopt an Aider-style lint/test-feedback repair.

### 3. Be a best-in-class MCP client and *stand on* the ecosystem — MEDIUM effort
The OSS-frameworks entry says it directly: don't build connectors, consume MCP servers to inherit thousands of integrations (registry counts are vendor-reported but large). **Why it matters:** closes the integration-breadth gap without per-connector work, and matches Cline/Continue/Goose/OpenCode. **Do:** first-class local + remote MCP with auto-OAuth + Dynamic Client Registration (OpenCode is the reference bar), plus a simple 1-click / GitHub-URL install flow (Cline is the reference bar).

### 4. Own "reproducible, version-controlled agent runs" for the terminal — MEDIUM effort
Goose "Recipes" (YAML macros) proved the demand and scaled ~60% inside Block (vendor-reported); OpenHands adds deterministic replay. No *terminal-native, MIT, local-first* tool cleanly owns reproducibility. **Why it matters:** reproducibility + Ronin's fail-closed gate is a strong "autonomous within rules" story for teams. **Do:** a plain-text, git-committable run spec (instructions + required MCP servers + model + approval policy) that replays deterministically.

### 5. Sandboxing / containment story — MEDIUM effort
Codex (Seatbelt/Landlock+seccomp) and Gemini/OpenHands (containers) set the bar; Ronin's containment is unstated. **Why it matters:** credibility for autonomous/auto-submit-style operation and enterprise trust. **Do:** document and, where missing, add an OS-level sandbox tier (macOS Seatbelt / Linux Landlock+seccomp) as an opt-in above the approval gate — mirrors Codex's two-layer "sandbox + approval_policy" design.

### 6. Lightweight semantic repo understanding — MEDIUM effort
Cursor/Roo/Continue/Aider all ship indexing; Ronin's is unstated. **Why it matters:** repo-scale tasks need it, and it can stay $0/local. **Do:** start with an Aider-style tree-sitter repo map (no external vector DB, no embedding key — unlike Roo's Qdrant dependency) to preserve the zero-dependency local ethos; optional embeddings later.

### 7. Capture the abandoned/ deprecating user bases — LOW effort (GTM)
**Roo Code** is archived/EOL (2026-05-15, redirecting users to ZooCode/Cline); **Continue's** OSS coding agent went read-only; **Gemini CLI** free/consumer users lose their path June 2026. **Why it matters:** three concrete pools of displaced users this cycle. **Do:** publish migration notes (AGENTS.md/CLAUDE.md compatibility, provider config mapping) targeting each, emphasizing "live, maintained, MIT, local-first."

### 8. Python-audience extensibility as a wedge — LOW effort (positioning)
Against Rust monoliths (Codex, Goose) and split builds (OpenCode), Ronin's single-language Python uv workspace is plausibly the most approachable to fork/extend for Python/ML shops. **Why it matters:** a narrow but real audience-fit lane. **Do:** ship a clean extension guide + example package in the 7-package workspace; make "hackable by Python developers" explicit.

**Guardrail across all lanes:** keep every public claim feature-present and honest. Ronin ships no head-to-head measurement, so avoid any "beats/parity/superiority/%/prod-ready" language until a real benchmark or verify harness backs it.
