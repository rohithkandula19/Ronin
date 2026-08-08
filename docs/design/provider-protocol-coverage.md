# Provider & protocol coverage — matrix, gaps, and protocol decisions

**Status: AUDIT FOR REVIEW. No implementation.** This is the pre-implementation
deliverable: the coverage matrix, the MCP remote-auth gap, the ACP conformance
gap list, and the A2A protocol decision. Nothing here has been built; the
provider-adapter and MCP-hardening work waits on review of this document.

**Method.** Every claim below is anchored to a `file:line` read out of the repo
at commit `7874ceb` (current `main`). Where something does not exist, it is
marked **absent** rather than described optimistically. External protocol
behavior is cited to its spec, not to memory.

---

## 1. Provider × capability matrix

There are exactly **two adapter classes** — `AnthropicProvider`
(`packages/agent-patterns/src/ronin_agent_patterns/providers/anthropic_provider.py:149`)
and `OpenAICompatProvider`
(`.../providers/openai_compat.py:85`) — plus the in-process
`EmbeddedProvider` (`packages/cli/src/ronin_cli/embedded_provider.py:357`).
Every cloud provider except Anthropic is `OpenAICompatProvider` with a different
`base_url` (`packages/cli/src/ronin_cli/runner.py:174`).

Legend: ✅ real · ⚠️ partial/caveated · ❌ absent · — n/a

| Provider | Adapter | Tool-call quirks | Reasoning/thinking | Streaming | Token counting | Argument drift |
|---|---|---|---|---|---|---|
| **Anthropic** | native SDK | ✅ shared normalizer + `tool_result` batching (`anthropic_provider.py:58`) | ✅ `thinking.budget_tokens` ladder; `low` disables (`effort.py:78`) | ✅ SDK stream (`anthropic_provider.py:241`) | ✅ real, **only** provider with cache tokens (`anthropic_provider.py:43`) | ✅ shared |
| **OpenAI** | OpenAI-compat | ✅ shared | ✅ `reasoning_effort`; `xhigh`→`high` clamp (`effort.py:86`) | ✅ SSE | ⚠️ real non-stream; **0/0 on streamed turns** — `stream_options.include_usage` never set (`openai_compat.py:247`) | ✅ shared |
| **Gemini** | OpenAI-compat shim (`/v1beta/openai`) | ✅ shared **+ only provider-specific accommodation**: `thought_signature` replay (`base.py:17`, `openai_compat.py:72`) | ❌ silently dropped (`effort.py:97`) | ✅ SSE | ⚠️ same stream gap | ✅ shared |
| **Moonshot / Kimi** | **❌ absent** | — | — | — | — | — |
| **Qwen (cloud/DashScope)** | **❌ absent** — Qwen exists only as local weights and as an OpenRouter *model id* | — | — | — | — | — |
| **Cerebras** | OpenAI-compat | ✅ shared | ❌ dropped | ✅ SSE + same-key sibling failover (`runner.py:31`) | ⚠️ same stream gap | ✅ shared |
| **Groq** | OpenAI-compat | ✅ shared + non-Python `User-Agent` (WAF 403s `python-httpx`) (`openai_compat.py:150`) | ❌ dropped | ✅ SSE | ⚠️ same stream gap | ✅ shared |
| **OpenRouter** | OpenAI-compat | ⚠️ shared, but **no `HTTP-Referer`/`X-Title`** and no model-routing prefs — a plain base_url swap | ❌ dropped (even for an o-series model routed through it) | ✅ SSE | ⚠️ same stream gap | ✅ shared |
| **Ollama** | OpenAI-compat + placeholder key | ✅ shared | ❌ dropped | ✅ SSE | ⚠️ same stream gap | ✅ shared |
| **local (embedded)** | `EmbeddedProvider` | ✅ canonical `ronin_dialect` parser; deliberately **refuses to repair** malformed blocks; optional GBNF lock | ❌ no `effort` field at all (`runner.py:167`) | ❌ **no native stream** — wraps `complete()` (`base.py:88`) | ⚠️ real on llama-cpp; **`{}` on mlx** (`embedded_provider.py:518`) | ✅ shared |
| Together, Fireworks, custom | OpenAI-compat | ✅ shared | ❌ dropped | ✅ SSE | ⚠️ same stream gap | ✅ shared |

**Shared quirk normalization** (provider-agnostic, no per-provider branches):
JSON-string vs object arguments (`openai_compat.py:165`); malformed-JSON
sentinel that produces a *coaching* error instead of running a tool
argument-less (`openai_compat.py:170`, `types.py:7`); 7 argument-alias families
for wrong parameter *names* (`base.py:11`); recovery of tool calls emitted as
**prose** for gpt-oss/Llama/Qwen/DeepSeek (`text_tools.py:82`); globally unique
synthetic call ids after duplicate `text-0` ids caused Anthropic 400s
(`text_tools.py:115`); parallel-tool-call delta accumulation
(`openai_compat.py:320`); repetition-loop guard (`repetition.py`); a single
provider-error funnel (`runner.py:63`).

### 1a. Cross-cutting defects found (not previously tracked)

| # | Defect | Evidence |
|---|---|---|
| P1 | Streamed OpenAI-compat turns report **0 tokens → $0 cost**; `stream_options.include_usage` is never sent | `openai_compat.py:247` |
| P2 | `AnthropicProvider` has **no `on_retry`**, so `attach_retry_notifier` silently no-ops — a ~60s backoff looks frozen | `runner.py:246` vs `anthropic_provider.py:104` |
| P3 | A 401 on Groq/Gemini/Cerebras tells the user to set the **wrong env var** (hardcoded Anthropic/OpenAI pair) despite a correct `PROVIDER_ENV_KEYS` map | `runner.py:85` vs `config.py:106` |
| P4 | Ollama is built as base `OpenAICompatProvider`, so the `OllamaProvider` subclass's `OLLAMA_BASE_URL` support is bypassed on the CLI path | `runner.py:172` vs `openai_compat.py:385` |
| P5 | `max_cost_usd` is **effectively dead** — it reads `usage["cost_usd"]`, which no provider ever emits | `react.py:459` |
| P6 | **Three divergent price tables** can disagree (e.g. groq $0 in one, $0.59/$0.79 in another) | `cost.py:14`, `usage.py:19`, `bench.py:19` |
| P7 | Preset drift: the `init` wizard omits gemini/cerebras/openrouter; onboarding defaults name an EOL model; `docs/providers.md` omits Together and Fireworks entirely | `main.py:310`, `onboard.py:22`, `docs/providers.md:7` |
| P8 | The effort→provider wiring itself is **untested** (`effort.py` is unit-tested in isolation; `grep effort packages/cli/tests/` → 0 hits) | `runner.py:158`, `:178` |

### 1b. What "systematic" would mean

Today coverage is *accidentally* uniform: everything non-Anthropic shares one
class, so quirks are handled once — which is good — but capability differences
(reasoning knobs, usage reporting, headers) are handled by **provider-name
`frozenset`s scattered across modules** (`effort.py:97`, `openai_compat.py:150`).
Adding Kimi or Qwen today means editing presets in one place and hoping no
capability set needs touching.

Proposal (for review, not built): a declarative **provider capability
descriptor** — one table entry per provider carrying `base_url`, env key,
reasoning-knob kind, `include_usage` support, required headers, and known quirks
— consumed by `build_single_provider`, `effort.py`, and the docs generator, so a
new provider is one row and the matrix in this document becomes generated rather
than hand-maintained. That directly fixes P1/P3/P4/P7 as a side effect.

---

## 2. MCP: current remote auth, and the gap

**The question was whether remote MCP auth is OAuth 2.1 + capability scopes +
audit logs. It is none of those three.**

**What exists.** Two transports: stdio (`mcp_client.py:110`) and single-endpoint
**Streamable HTTP** (`mcp_remote.py:51`), protocol pinned to `2024-11-05`.
Remote auth is a **static header persisted to disk**: `$MCP_TOKEN` is read once
at `mcp add-remote` time and the literal bearer value is written into
`.ronin/mcp.json` (`main.py:1892`, `mcp_client.py:272`), then merged unchanged at
connect time (`mcp_remote.py:67`).

| Requirement | Status | Evidence |
|---|---|---|
| OAuth 2.1 authorization code + PKCE | **❌ absent** | repo-wide grep for `oauth\|pkce\|code_verifier`: zero implementation hits |
| Token refresh / rotation | **❌ absent** | token is a literal string at rest |
| Resource indicators (RFC 8707) | **❌ absent** | — |
| Dynamic Client Registration (RFC 7591) | **❌ absent** | — |
| `401` + `WWW-Authenticate` discovery / `/.well-known/oauth-protected-resource` | **❌ absent** | — |
| Capability **scopes** (which MCP tools a session may use) | **❌ absent** — every tool a trusted server advertises is registered | `mcp_client.py:364` |
| Audit log of MCP tool calls | **❌ absent** — `call_tool()` writes nothing anywhere | `mcp_client.py:200`, `mcp_remote.py:106` |
| TLS/redirect/SSRF hardening on the remote path | **❌ absent** — no scheme validation, so an `http://` URL sends the bearer in cleartext | `mcp_remote.py:67` |

**What is genuinely strong** (and must not regress): a content-hash **trust gate**
that refuses to start *any* server from an untrusted `.ronin/mcp.json`, because
a repo-committed config is arbitrary code execution (`mcp_client.py:364`);
per-server env **scoping by name** for local servers, so an undeclared secret is
invisible to the child (`mcp_client.py:76`, tested at
`tests/test_mcp_env_scope.py:85`); **all MCP tools forced `sensitive=True` with
`readOnlyHint` deliberately distrusted** (`mcp_client.py:349`); and a
**destructive floor** that inspects nested executable payloads on every MCP tool
and is **not waivable by `--yolo`** (`approvals.py:467`, tested at
`tests/test_floor_scope.py:74`).

**Notable asymmetry.** Local servers got by-name secret scoping *precisely so a
key never lands on disk*; the remote path has no equivalent and bakes the
credential in. `docs/INTEGRATIONS.md:27` documents the `$MCP_TOKEN` convenience
without noting this, so a reader would reasonably assume the same protection
applies. It does not.

**Plan to close it (for review, not built), smallest-first:**

1. **Store remote credentials by reference, not value** — a `passEnv`-equivalent
   for headers, so `.ronin/mcp.json` holds `MCP_TOKEN` (a name) and resolution
   happens at connect time. Fixes the at-rest credential with no protocol work.
2. **Refuse cleartext bearers** — require `https://` (or explicit localhost
   opt-in) before attaching an auth header; validate redirects.
3. **OAuth 2.1 as a discovery-driven path** — on `401 + WWW-Authenticate`, probe
   `/.well-known/oauth-protected-resource`, then authorization code **+ PKCE**
   with resource indicators and refresh; tokens in the existing trust store, not
   the config file. DCR only if a target server requires it.
4. **Per-tool allowlist** — an explicit `tools:` allowlist per server in the
   config, defaulting to "all advertised" *only* for servers already trusted, so
   scope can be tightened without breaking existing setups.
5. **Route MCP calls through the hash-chained trail** — server identity, tool,
   approval decision, timestamp. This is the prerequisite for the unified policy
   engine having anything to audit.

Also worth fixing while here: `[a]lways` grants for MCP tools persist as
`match="*"` (any arguments, forever) unlike shell/path tools which persist a
literal (`permissions.py:70`, `:122`).

---

## 3. ACP: what a client like Zed expects that `ronin acp` does not provide

`ronin acp` is real (`main.py:7866` → `acp.py`, 430 lines, 5 tests) but is a
**narrow read-only slice**. Implemented: `initialize`, `session/new`,
`session/load`, `session/prompt`, `session/cancel`, and exactly one
`session/update` variant (`agent_message_chunk`, `acp.py:163`).

**Missing — verified by repo-wide grep returning zero matches:**

| Expectation | Status |
|---|---|
| `session/update` → `tool_call`, `tool_call_update` | ❌ absent — the editor never learns which tools ran or which files were touched |
| `session/update` → `plan` | ❌ absent |
| `session/update` → `agent_thought_chunk` | ❌ absent |
| `session/update` → `available_commands_update`, `current_mode_update` | ❌ absent |
| `session/request_permission` | ❌ absent — no permission flow; the agent is hard-pinned read-only (`acp.py:399`) or to detached-worktree proposals |
| `fs/read_text_file`, `fs/write_text_file` | ❌ absent — `clientCapabilities` is never read (`acp.py:336`), so unsaved editor buffers are invisible |
| `terminal/create|output|wait_for_exit|kill|release` | ❌ absent |
| `authenticate` | ❌ absent |
| ACP session **modes** (`modes` + `session/set_mode`) | ❌ absent — replaced by a bespoke `roninMode` param no standard client can set (`acp.py:222`) |
| Slash / `availableCommands` | ❌ absent |
| Non-text prompt content (image/audio/resource) | ❌ rejected (`acp.py:256`), and correctly advertised `false` |
| MCP server passthrough | ⚠️ **deliberately rejected** with `-32602` (`acp.py:204`) — a documented security decision, not an oversight |

**Four protocol deviations that would break a real client:**

1. **`protocolVersion` is asserted, not negotiated** — anything but exactly `1`
   gets a hard `-32602` (`acp.py:337`), so a client that bumps its version gets
   an error instead of a downgrade.
2. **Streaming is batched** — `emit` only appends to a list; all updates are
   constructed after the run returns (`acp.py:274`, `:280`), so
   `agent_message_chunk` is cosmetic and the editor gets the whole turn at once.
3. **`session/cancel` is answered as a request** though ACP defines it as a
   notification, and it cannot interrupt an in-flight turn — it only sets a flag
   for the *next* prompt (`acp.py:247`, `:374`).
4. **`serve_stdio` replies to everything**, including genuine notifications
   (`acp.py:421`).

Also: `docs/grok_build_runtime_reference.md:28` still claims Ronin has "no ACP"
and `:64` lists adopting ACP as a deferred non-goal — both stale versus shipped
code. Zed is never named anywhere in the repo, and the shipped VS Code extension
does not use ACP, so **no client currently exercises this bridge**.

---

## 4. A2A: evaluated, chosen, and deliberately not built

**Decision: A2A is the protocol for Phase 7's agent-exposure work. We will not
invent a bespoke format.** Recorded here so the choice precedes the code.

**Current state: A2A has zero footprint** — case-insensitive grep for `a2a`,
`agent2agent`, `AgentCard`, `.well-known/agent` across the repo returns no
semantic matches. It is not implemented, not deferred, and not listed as a
non-goal; it was simply never considered. The competitive research covers ACP and
MCP but never A2A (`docs/research/competitive_matrix.md`,
`docs/research/gap_analysis.md:26`).

**What it would replace.** Three unrelated bespoke HTTP surfaces already do a
fraction of A2A's job, with no discovery document, capability card, or
interop story:

| Surface | What it exposes |
|---|---|
| `packages/cli/src/ronin_cli/server.py:49` | `POST /ask` — closest analogue to `message/send`; capability info is an ad-hoc `GET /health` |
| `apps/api/csk_api/main.py:364` | `POST /webhooks/agent` — comments at `:358` describe hand-writing thin per-provider webhook adapters, exactly the N-adapters problem A2A targets |
| `packages/cli/src/ronin_cli/api_gateway.py:51` | read-only mission introspection; no task submission |

**Why A2A rather than a bespoke format:** it is the only agent-to-agent protocol
with an Agent Card discovery document, a defined task lifecycle, and streaming
plus push-notification transports already specified — so "expose a specialist
agent" becomes a conformance exercise instead of an API-design exercise, and the
three surfaces above can converge on one contract. It composes with MCP rather
than competing (MCP = tools a agent calls; A2A = peer agents it delegates to).

**Sequencing constraint, stated plainly:** the Phase 7 priority list makes the
A2A gateway (#6) depend on the unified policy engine (#2), and that ordering is
correct — exposing agents remotely *before* there is one place evaluating
capability, cost, and provider rules would mean shipping a remote attack surface
governed by the N independent checks catalogued in the policy-engine design doc.
**No A2A code should be written until #2 exists.**

---

## 5. Recommendation, and what is deliberately not being done

Recommended order once approved: **(a)** the capability-descriptor refactor plus
the Kimi K2 and Qwen-cloud adapters as its first two consumers (they are one row
each under the new shape, and prove the shape); **(b)** MCP hardening steps 1–2
and 5 from §2 (credential-by-reference, https enforcement, audit routing) since
they are small and unblock the policy engine; **(c)** OAuth 2.1 (step 3) and the
per-tool allowlist (step 4) as a second pass.

Not being done in this pass, on purpose: ACP conformance work (no client
exercises the bridge yet, so the gap list is documentation until one does), and
anything A2A (blocked on the policy engine by design).

**Definition of done when this is approved:** this matrix regenerated with the
gap column cleared, one deterministic `FakeProvider`-style adapter test per new
provider (no live API calls in CI — the pattern is
`packages/agent-patterns/src/ronin_agent_patterns/providers/fake.py:13`), and
exact pass/fail counts reported.
