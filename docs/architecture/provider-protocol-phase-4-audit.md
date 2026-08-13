# Provider and Protocol Coverage Audit

**Status:** Proposed for review. This is a Phase 4 design checkpoint. No new
provider adapter, MCP auth flow, or A2A endpoint is implemented by this
document.

## Evidence and Terms

The matrix reflects the current repository, especially
`packages/agent-patterns/src/ronin_agent_patterns/providers/`,
`packages/agent-patterns/src/ronin_agent_patterns/effort.py`,
`packages/cli/src/ronin_cli/runner.py`, `mcp_remote.py`, and `acp.py`.

| Term | Meaning |
| --- | --- |
| **Dedicated** | A provider-specific request/response adapter with deterministic unit tests. |
| **Generic** | Routed through `OpenAICompatProvider`; it has generic tests, not a provider-specific protocol fixture. |
| **Partial** | A narrow provider quirk is carried, but the overall provider wire contract is not independently verified. |
| **Estimated** | Ronin's documented fallback token counter, not a provider guarantee. |
| **Gap** | Missing, unverified, or deliberately unsupported. It must not be represented as full coverage. |

## Current Provider Coverage Matrix

| Provider | Tool-calling quirks handled today | Reasoning / thinking | Streaming | Token counting | Known argument-drift issues and explicit gaps |
| --- | --- | --- | --- | --- | --- |
| Anthropic | **Dedicated.** Maps `tool_use`; batches consecutive neutral tool results into Anthropic `tool_result` blocks in one user turn; deterministic tests cover both. | **Dedicated.** Normalized effort maps to Anthropic `thinking` with a bounded `budget_tokens` ladder. | **Dedicated native stream.** Text deltas stream natively; final message supplies assembled tool calls and usage. | **Native.** Messages `count_tokens`; failures fall back to the documented estimate. | No provider-specific regression fixture for interleaved streamed tool-use blocks, thinking signatures, or every Messages content-block variant. The adapter only models Ronin's text/tool contract, not the complete Anthropic multimodal or beta surface. |
| OpenAI | **Dedicated first-party behavior within the generic adapter.** Standard `tool_calls[].function.arguments` is parsed, fragmented stream arguments are assembled, malformed JSON is preserved as an error sentinel, and text-encoded calls have a bounded fallback parser. | **Dedicated.** Normalized effort maps to `reasoning_effort`; `xhigh` explicitly clamps to `high`. | **Native OpenAI-compatible SSE.** Text and tool-call deltas are assembled; mid-stream retries emit a neutral reset. | **Native only for first-party OpenAI.** Uses `POST /responses/input_tokens`; completion traffic remains Chat Completions. | No Responses completion adapter. The adapter assumes Chat Completions field names, one choice, stringified function arguments, and standard `data:` SSE. New Responses-only item types and nonstandard tool formats are rejected or unmodeled. |
| Gemini | **Partial generic.** Uses Google's OpenAI-compatible endpoint. The neutral tool call retains and replays `extra_content` so a Gemini thought signature can survive a tool-result turn; a deterministic round-trip fixture exists. | **Explicit no-op.** No reasoning request parameter is emitted. `preserve_tool_state=True` is declared for thought-signature replay. | **Generic native SSE** through the OpenAI-compatible parser. | **Estimated.** No Gemini-native counter is implemented. | No Gemini-specific adapter or contract fixture verifies the exact current thought-signature field, function-call ordering, structured-content shape, or streaming tool-call deltas against Gemini. The `extra_content` pass-through is a narrow compatibility mechanism, not proven complete coverage. |
| Moonshot / Kimi K2 | **Gap.** No `kimi` provider preset, adapter, credentials entry, or deterministic provider fixture exists. It can only be attempted through a manually configured compatible endpoint. | **Gap.** No Kimi reasoning capability declaration. | **Unverified generic path only** when manually configured. | **Estimated** on a manually configured endpoint. | No first-class base URL/auth model, tool schema translation, tool-result replay, argument parser, stream parser, model capability catalog, or CI fixture. |
| Qwen / Qwen Code | **Split coverage.** No DashScope/Qwen Code cloud adapter exists. Qwen may run via OpenRouter/Ollama generic compatibility. The embedded local Qwen path has a separate, tested ChatML `<tool_call>` dialect with object-valued arguments and native tool-role history. | **Gap for cloud Qwen.** No Qwen reasoning capability declaration. | **Generic SSE** for OpenAI-compatible routes. The embedded local provider's default stream is not a native incremental Qwen stream. | **Estimated** for cloud-compatible and local routes. | No first-class DashScope/Qwen Code route, auth/header behavior, cloud tool-call fixture, or stream fixture. The embedded dialect is not interchangeable with Qwen Code's hosted API, so it must not be used to claim hosted Qwen compatibility. |
| Cerebras | **Generic.** Configured preset routes through `OpenAICompatProvider`; standard parser, malformed-JSON sentinel, text-call fallback, and generic tool-history replay apply. | **Explicit no-op.** | **Generic native SSE.** | **Estimated.** | No Cerebras-specific request/response fixture or capability catalog. Any model-specific argument, tool-choice, usage, or SSE variation is currently unverified. |
| Groq | **Generic.** Configured preset routes through `OpenAICompatProvider`; it also benefits from generic retry behavior and custom user-agent handling. | **Explicit no-op.** | **Generic native SSE.** | **Estimated.** | No Groq-specific tool-call or stream fixture. The code has an operational WAF/user-agent workaround, but no dedicated argument-drift normalization. |
| OpenRouter | **Generic.** Configured preset routes through `OpenAICompatProvider`; standard generic tool handling applies. | **Explicit no-op.** | **Generic native SSE.** | **Estimated.** | OpenRouter can route the same request to many upstream model families. Ronin has no per-routed-model capability declaration, no upstream transform awareness, and no deterministic fixture for provider/model argument drift. |
| Ollama | **Generic plus thin convenience class.** `OllamaProvider` only supplies the local OpenAI-compatible base URL; generic parser/replay applies. The separate embedded Qwen path is not Ollama. | **Explicit no-op.** | **Generic native SSE** through Ollama's OpenAI-compatible endpoint. | **Estimated** unless a future local tokenizer is configured. | No native Ollama `/api/chat` adapter or model-template capability detection. Tool-call behavior varies by locally installed model, and the generic fallback text parser cannot prove the selected model honors structured tools. |

### What the Existing Generic Normalization Actually Covers

`OpenAICompatProvider` currently centralizes these cross-provider defenses:

1. Standard OpenAI Chat Completions request rendering and `Authorization:
   Bearer` headers.
2. Tool-call history serialization, including `tool_call_id` for tool results.
3. JSON parsing for string arguments, with malformed source preserved instead of
   coercing it to `{}`.
4. Fragmented streamed `tool_calls` argument accumulation by index.
5. A bounded text-tool fallback for models that emit a call in assistant text.
6. Retry/reset handling for 429 and selected 5xx statuses.
7. Opaque `provider_meta` replay for the currently known Gemini thought-signature
   path.

It does **not** establish a capability contract per backend. In particular it
does not validate provider-specific tool-choice values, JSON Schema subsets,
parallel-call semantics, content-block encodings, native reasoning fields,
model-specific usage fields, or provider-specific error shapes. Treating every
endpoint that accepts `/chat/completions` as equivalent is the principal Phase 4
gap.

## Required Adapter Shape After Approval

Phase 4 should add an explicit `ProviderProfile` and a small adapter per
provider family, while preserving the existing neutral `Message`, `ToolCall`,
`LLMResponse`, and `StreamEvent` contract. A profile will declare:

```python
ProviderProfile(
    name="kimi",
    transport="openai_chat_completions",
    auth="bearer",
    tool_call_mode="openai_function" | "qwen_chatml" | "anthropic_blocks",
    tool_result_mode="tool_role" | "anthropic_user_blocks",
    argument_encoding="json_string" | "json_object",
    stream_mode="openai_sse" | "anthropic_sse" | "none",
    reasoning_mode="none" | "openai_effort" | "anthropic_thinking",
    preserve_tool_state=False,
    token_counter="native" | "tokenizer" | "estimated",
)
```

This is a declaration and normalization boundary, not a promise that all
providers have identical features. Every profile must state a supported subset;
an unsupported feature becomes an explicit no-op or validation error before an
invalid provider parameter is emitted.

The first implementation priority is:

1. Kimi K2 adapter and deterministic fixture.
2. DashScope/Qwen Code adapter and deterministic fixture, kept separate from
   the embedded Qwen ChatML dialect.
3. Dedicated profiles and fixtures for Gemini, Cerebras, Groq, OpenRouter, and
   Ollama, even when their current transport stays OpenAI-compatible.
4. A table-driven provider conformance suite: request rendering, normal response,
   fragmented streaming call, malformed argument handling, tool-result replay,
   no-op/valid reasoning behavior, and count provenance. The suite uses a
   FakeProvider-style transport fixture and makes no live provider call in CI.

## MCP Remote Authentication Audit

### Current State: Not Sufficient for Remote MCP

Ronin supports local stdio MCP and remote Streamable HTTP transport. Local MCP
has useful protections: repository config trust, a minimal inherited environment,
and all discovered MCP tools are marked sensitive for Ronin's approval path.

Remote MCP is not yet production-grade authorization:

| Required remote-MCP property | Current implementation | Status |
| --- | --- | --- |
| Streamable HTTP handshake and session header | `MCPRemoteClient` sends JSON-RPC, accepts JSON/SSE, and retains `Mcp-Session-Id`. | Partial transport coverage. |
| HTTPS-only remote endpoint policy | Any URL can be configured. | **Gap.** |
| OAuth 2.1 protected-resource discovery | No `401` challenge parsing or Protected Resource Metadata lookup. | **Gap.** |
| Authorization-server metadata / OIDC discovery | Not implemented. | **Gap.** |
| Authorization Code plus PKCE S256 and state verification | Not implemented. | **Gap.** |
| Dynamic/client-ID metadata registration strategy | Not implemented. | **Gap.** |
| Scoped access tokens and refresh tokens in secure storage | `--header` values are stored directly in `.ronin/mcp.json`; no token lifecycle. | **Gap.** |
| Capability scopes tied to remotely exposed tools | All MCP tools are approval-sensitive, but no OAuth or per-server capability grant exists. | **Gap.** |
| Remote MCP audit record | No dedicated remote server/resource/scope/tool outcome ledger exists. | **Gap.** |
| Safe local configuration trust | Present, but it is not substitute authentication. A user-added remote config is trusted and static headers remain plaintext configuration. | Partial. |

The MCP authorization specification requires protected MCP servers to act as
OAuth 2.1 resource servers and clients to use Protected Resource Metadata for
authorization-server discovery. It also requires PKCE support for the
authorization-code flow. See the [MCP authorization
specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).

### Approved-Design Candidate for Closing the MCP Gap

When implementation is approved, remote MCP changes will follow this sequence:

1. **Secure configuration boundary.** Require HTTPS for remote servers except an
   explicit localhost development exception. Replace persisted raw `headers`
   with a server ID and credential reference. Existing static-header entries are
   shown as a migration warning and are not silently treated as OAuth grants.
2. **OAuth 2.1 discovery.** On `401`, parse `WWW-Authenticate` for
   `resource_metadata`; otherwise use the required Protected Resource Metadata
   well-known discovery locations. Discover authorization-server metadata via
   OAuth AS metadata or OIDC discovery.
3. **Authorization flow.** Use Authorization Code with PKCE S256, state and
   exact redirect validation. Prefer client-ID metadata; use Dynamic Client
   Registration only when the authorization server advertises it. Refuse the
   flow when required PKCE capability cannot be verified.
4. **Capability grant.** Persist an encrypted/OS-secret-store credential
   reference with resource, issuer, audience, expiry, refresh metadata, and the
   least scopes requested by the server. Add a local Ronin grant that limits the
   server and advertised tools allowed to consume that token. OAuth scope alone
   never bypasses Ronin's existing per-tool approval floor.
5. **Audit.** Add append-only safe events for discovery, grant, refresh,
   denial, tool admission, tool outcome, and revocation. Events include remote
   server ID, resource/issuer digest, requested/granted scope names, tool name,
   request ID, status, and duration. They never include tokens, headers, raw
   arguments, tool results, or prompt content.
6. **Failure behavior.** Missing, expired, wrong-audience, insufficient-scope,
   refresh-failed, or discovery-ambiguous credentials deny the remote MCP call
   before it is made. No fallback to an old static bearer header occurs.
7. **Tests.** Deterministic fixtures cover PRM and AS discovery, PKCE/state,
   scope downscoping, expired/invalid tokens, refresh, redirect mismatch,
   HTTPS policy, credential redaction, and audit redaction. No OAuth server or
   MCP provider is contacted in CI.

## ACP Client Gap List

`ronin acp` correctly provides a local stdio JSON-RPC bridge with initialize,
session creation/load, text prompts, text chunks, and a cancellation request
recorded between turns. It deliberately restricts workspaces and rejects
editor-supplied MCP definitions. That is a conservative security posture, but
it is not a feature-complete ACP agent for Zed or other ACP clients.

| ACP client expectation / stable v1 surface | Ronin now | Specific gap |
| --- | --- | --- |
| Text prompt and agent-message chunks | Supported. | No message IDs, rich agent thought chunks, or clear standard end-of-message boundary beyond turn completion. |
| Tool-call and tool-call-update events | Not emitted. | Clients cannot render live commands, file edits, status, locations, diffs, or completed tool output from Ronin's real tool loop. `ronin/activity` is a private notification, not an ACP replacement. |
| Client permission request for a gated action | Not implemented. | Ronin has local approval gates, but ACP has no `session/request_permission` bridge or response handling; proposal mode sidesteps direct edits rather than producing editor-native approvals. |
| Editor-provided MCP configuration | Rejected intentionally. | ACP clients commonly pass their configured MCP servers. Ronin needs an explicit trust/capability import flow, not blanket acceptance and not permanent rejection, before it can behave like a full ACP integration. |
| Standard session modes and configuration options | Only private `roninMode` (`read_only` or `proposal`). | Does not advertise/update ACP session modes, model/provider selectors, approval mode, or other standard config options. |
| Plan updates | Not emitted. | A client cannot render the agent's current plan or task progress in the standard ACP plan shape. |
| Usage/context updates | Only private completion activity contains usage. | Does not emit standard ACP context/cost usage updates from the Phase 1 context kernel. |
| Session lifecycle | `session/new`, `session/load`, and a local persistence format supported. | No standard `session/list`, `session/close`, `session/delete`, `session/resume`, transcript replay after load, session title update, or terminal-session history UI update. |
| Cancellation | A cancel flag prevents the next request after it is received. | Active provider calls and active tools are not cooperatively interrupted through an ACP cancellation token; stdio processing is serialized. |
| Rich prompt blocks | Explicitly advertises image/audio/embedded context as unsupported. | No image, audio, file/resource, or embedded editor context support. This is honestly declared rather than silently ignored. |
| Editor filesystem and terminal capabilities | Not used. | Ronin neither consumes a client filesystem/terminal capability nor emits terminal output updates. Its current local coding tools remain its execution source. |
| Parallel sessions | Session objects can coexist. | The stdio loop processes one request synchronously, so independent turns do not run concurrently. |
| Streamable HTTP / WebSocket ACP transport | Not present. | `ronin acp` is stdio only. This does not block local Zed integration, but it blocks remote/editor-server transport use cases. |

ACP is designed for a bidirectional editor-agent relationship, including tool
permission requests and editor-provided MCP configuration. Its documented
architecture and transport work are the references for this gap list:
[ACP architecture](https://agentclientprotocol.com/get-started/architecture),
[ACP v1 documentation index](https://agentclientprotocol.com/llms.txt), and
[Streamable HTTP/WebSocket transport proposal](https://agentclientprotocol.com/rfds/streamable-http-websocket-transport).

### ACP Sequencing Decision

Phase 4 does not broaden ACP while provider and MCP contract work is underway.
The future ACP implementation should first add typed tool/approval events and
active cancellation on top of Ronin's existing safety gates. Editor-supplied MCP
must wait for the OAuth/capability-grant design above, then import only an
explicitly approved server grant. Ronin will not accept arbitrary editor MCP
configuration as a shortcut around local trust or remote scopes.

## A2A Evaluation and Phase 7 Decision

**Decision:** Phase 7 agent exposure will use the Agent2Agent (A2A) protocol;
Ronin will not invent a bespoke agent-to-agent JSON format.

Why this fits Ronin:

- MCP is the tool/resource protocol. A2A is the peer-agent task, discovery, and
  artifact protocol; they are complementary rather than interchangeable.
- A2A defines an Agent Card, task lifecycle, task cancellation, standard
  message/task/artifact objects, optional streaming, and published security
  declarations. Those map to Ronin's durable missions, typed handoffs, evidence
  bundles, budgets, and hosted-key policy far better than an ad hoc RPC surface.
- A future external Ronin agent will expose only approved skills. Its Agent Card
  will declare authentication and interfaces, while the hosted API-key and
  budget controls remain the enforcement authority.

Phase 7 implementation scope is intentionally deferred. Before writing it,
Ronin will write an A2A-specific threat model and contract covering Agent Card
discovery, tenant/key binding, skill-level authorization, task-budget
reservation, durable `message/send` / `tasks/get` / `tasks/cancel`, streaming
and resubscription, artifact redaction, callback/webhook SSRF controls, and
safe audit evidence. The current A2A specification requires an Agent Card and
the core send/get/cancel methods for compliant agents; streaming is an optional
declared capability. See the [A2A specification](https://a2a-protocol.org/v0.3.0/specification/).

## Review Gate and Definition of Done

This checkpoint is complete only as an audit and design proposal:

- Full requested provider matrix is listed above, with every gap called out.
- Remote MCP OAuth/capability/audit gaps and a failure-closed implementation
  plan are listed above.
- ACP limitations are itemized against client-facing protocol surfaces.
- A2A is selected and documented for Phase 7 only; no bespoke agent-exposure
  protocol is planned.

No provider, MCP, ACP, or A2A code was changed. No tests were run because this
checkpoint makes no executable change. After approval, Phase 4 will update this
matrix with the closed gaps and report exact full-suite pass/fail/skip counts.
