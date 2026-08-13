# Hosted Gateway Threat Model

**Status:** Proposed for review. This document precedes all hosted-gateway
authentication and authorization implementation.

## Scope and Security Boundary

The proposed hosted gateway adds completion endpoints to `apps/api`:

- `POST /v1/chat/completions` (canonical OpenAI-compatible contract)
- `POST /v1/messages` (bounded Anthropic Messages adapter)

It accepts a Ronin project/tenant API key and calls an already-configured Ronin
provider route. It is a stateless completion API, not a remote-control API.
The endpoint does **not** grant access to a checkout, shell, filesystem, local
network, MCP server, mission execution, approval queue, or provider secrets.
Caller-provided tools are schemas for the selected model; they are never
executed by Ronin.

This boundary is intentionally narrower than Ronin's local coding-agent and
mission-control surfaces. Those surfaces retain their separate approval,
sandbox, and audit protections.

## Assets and Trust Boundaries

| Asset | Why it matters | Required protection |
| --- | --- | --- |
| Raw `rnk_live_...` key | Authorizes provider spend within its policy | Display once, salted digest at rest, TLS only, redact from logs/errors. |
| Key policy and quota state | Limits spend and concurrency | Transactional durable store, atomic admission, deny if unavailable. |
| Provider credentials | Let Ronin route a completion | Server-side vault/config only; never returned or delegated to the caller. |
| Prompt, completion, tool arguments/results | May contain customer data or secrets | Do not persist in gateway audit logs; apply configured provider data policy. |
| Model/provider allowlist and price catalog | Determines where data may egress and how cost is bounded | Versioned, operator-controlled, validated at admission. |
| Audit events | Security investigation and billing evidence | Append-only safe metadata, access controlled, no raw payloads. |
| Host and other projects/tenants | Must not be reachable through a completion key | Strong project/tenant binding and no gateway-exposed agent tools. |

## Actors

| Actor | Capability assumed | Security objective |
| --- | --- | --- |
| Legitimate key holder | Valid scoped key | Use only its allowed models within published limits. |
| Malicious key holder | Valid scoped key, arbitrary request bodies | Cannot exceed policy, execute tools, access another tenant, or reveal server secrets. |
| Holder of a leaked key | Same as its owner until revoked | Blast radius is bounded; rotation/revocation stops new admissions. |
| Unauthenticated internet client | Can send requests and guess headers | Cannot enumerate keys, models, tenants, or quota state. |
| Malicious prompt/tool definition | Controls model-visible text and JSON schema | Cannot cause Ronin to execute local actions or pollute logs with sensitive data. |
| Compromised upstream provider | Receives routed request content | Is constrained to explicitly selected provider/model and configured data egress policy. |
| Failed or partitioned quota/audit store | Makes authorization accounting uncertain | Causes admission denial, never an unmetered completion. |
| Operator with deployment access | Can configure routes and policy | Is outside API-key isolation; operational access must be separately controlled and audited. |

## Abuse Cases and Controls

| Threat | What a malicious or compromised key holder could attempt | Control | Residual risk |
| --- | --- | --- | --- |
| Spend exhaustion | Make many expensive, long, or streaming requests | Atomic preflight rate, concurrency, token, and cost reservations. Output capacity is reserved before provider invocation. | A valid key can spend up to its explicit remaining budget. |
| Cross-tenant data access | Request a different project, key, model route, or workspace | Key is project/tenant bound; gateway has no project-path parameter or workspace tools; model aliases are policy allowed. | An operator who intentionally maps a key to a shared route remains privileged. |
| Shell/filesystem/MCP abuse | Put a command in a prompt or function definition | Gateway does not run agent tools, caller functions, MCP, shell, or filesystem tools. It returns a requested tool call to the client. | The external client may itself execute unsafe tools; that is outside Ronin's gateway boundary. |
| Provider credential exfiltration | Ask the model to reveal keys or route to an attacker URL | Credentials never enter request context; model aliases resolve only to server-configured routes; no caller-supplied provider URL or headers. | Prompt content is still sent to the selected configured provider. |
| Key guessing or enumeration | Try malformed tokens and inspect differences | High-entropy tokens, constant-time digest comparison, uniform invalid-key response, rate limiting, no key lookup endpoint. | A leaked key is valid until it is revoked or expires. |
| Replay of a stolen key | Reuse captured bearer/header credential | TLS required; short expiry encouraged; rotation/revocation; optional network restrictions later. | A bearer key remains replayable by design while active. |
| Quota race | Send parallel requests to exceed remaining budget | A single transactional reservation includes request rate, active lease, token upper bound, and cost upper bound. | Conservative over-reservation can temporarily deny capacity. |
| Store outage or partition | Exploit an unavailable quota service to bypass limits | Gateway denies before provider call with `503 quota_store_unavailable`; it never falls back to process memory or JSON in hosted mode. | Availability decreases during store incidents by design. |
| Crash after admission | Leave a lease or hide actual spend | Durable lease with expiry, request ID, and reconciliation record; unconfirmed releases remain charged/reserved conservatively. | Temporary overcharge/denial until reconciliation or lease expiry. |
| Stream interruption | Disconnect after a provider call starts to avoid accounting | Server cancels upstream when possible, retains reservation until durable settlement, and records outcome without payload. | Some provider usage may already have occurred. |
| Log exfiltration | Send secrets in prompts, headers, or tool arguments | Structured audit allowlist; raw bodies, raw keys, and headers are excluded. Opaque `user` is keyed-digested, not stored. | Operational logs must also be configured to redact headers. |
| Parser/resource exhaustion | Use huge JSON, deeply nested schemas, or infinite streaming | Body size/depth/message/tool/schema limits, request timeouts, output caps, concurrency reservation, upstream cancellation. | A bounded request still consumes some ingress and validation CPU. |
| Provider parameter smuggling | Use unsupported fields to alter upstream behavior | Strict dialect models and explicit `422 unsupported_parameter`; no blind parameter pass-through. | New provider features require a reviewed contract update. |

## Leaked-Key Blast Radius

A leaked hosted key can make completions only within all of these boundaries:

- its project/tenant binding;
- its `gateway:chat` scope;
- its model/provider allowlist;
- its per-minute rate limit and maximum concurrent requests;
- its maximum input/output request size;
- its remaining cumulative token and USD budgets;
- its expiry and revocation state.

It cannot use Ronin's code execution, write gates, approvals, local checkout,
MCP tools, remote workers, mission runner, or provider credential management.
It can submit whatever prompt content its holder knows, and that content can be
sent to an allowed provider. This is the principal data-exposure risk and must
be reflected in deployment-level provider/data-retention policy.

Revocation denies new requests as soon as it is durable in the key authority.
In-flight requests are not magically reversible; their leases remain tracked
and are reconciled. Operators should rotate a suspected key immediately and
inspect safe audit metadata by key ID and request ID.

## Failure-Closed Quota Protocol

### Required authority

The current `ronin util api-keys` local implementation owns token format,
digest, scope, expiry, revocation, policy, usage, leases, and audit semantics.
It is suitable for a single-project, single-process local read-only gateway.

Hosted completions require the same `ApiKeyStore` contract backed by a
transactional durable service (for example, the `apps/api` database with row
locks/serializable transactions). The hosted server must refuse to enable
completion routes without that backend. It must not substitute local JSON,
per-process memory, cache-only counters, or a permissive fallback.

### Admission transaction

Before a provider request, one transaction must:

1. Authenticate the key digest, project/tenant binding, active state, scope,
   and allowed model alias.
2. Validate request size and supported parameters.
3. Count input tokens with Ronin's provider-aware counter. Counter provenance
   is persisted as `native`, `tokenizer`, or **`estimated`**. The documented
   fallback is an estimate, never a guarantee.
4. Calculate the maximum output token allowance: the lesser of request,
   key-policy, deployment, and model limits.
5. Compute `reserved_tokens = input + maximum_output` and a monetary upper
   bound from an active, versioned model price entry. An unknown price with an
   enabled cost budget is an admission denial, not a zero-dollar estimate.
6. Atomically check and increment rolling request count, active lease count,
   reserved token total, and reserved cost total; record a lease ID, request
   ID, expiry, price-catalog version, and counter provenance.
7. Persist the safe `gateway.request_admitted` audit event before provider
   invocation.

Any timeout, unavailable store, transaction conflict that cannot be retried
within a short bounded window, invalid persisted policy, absent price entry,
or audit-admission write failure returns a safe `503` or `429` response before
calling a provider. This is deny-by-default.

### Settlement and recovery

After provider completion, cancellation, or known upstream failure, Ronin
writes actual provider usage when available. When exact usage is unavailable,
it uses the same labeled counter method from admission. It then atomically:

- records outcome, token/cost actuals, duration, and stream status;
- converts the reservation to actual usage; and
- releases only the unused token/cost/concurrency reservation.

If durable settlement cannot be confirmed, the reservation remains held. It is
not released in memory and the gateway does not claim a successful settled
request. A bounded reconciliation job retries durable settlement from the
lease record. Expired leases are reconciled conservatively: no unverified
unused capacity is returned automatically when a provider invocation might
have occurred. An operator-visible reconciliation state documents any manual
resolution.

This preference can cause temporary over-reservation or 503s during a store
incident. That is intentional: exceeding a key's declared budget is worse than
temporarily refusing a request.

### Streaming

Streaming admission and reservation are complete before the first byte of SSE.
The upstream call is tied to the lease/request ID. On client disconnect, Ronin
cancels the upstream call where the provider supports cancellation, records a
safe disconnected outcome, and settles or conservatively retains the lease.
The server never releases a lease merely because the client connection closed.

## Audit Event Contract

Gateway audit events follow the mission-control practice: append-only safe
metadata, never raw conversational payloads. Every request produces an event
for admission and a terminal event for completion, cancellation, rejection, or
failure.

Allowed fields:

```json
{
  "event": "gateway.request_completed",
  "at": "2026-08-07T16:20:00Z",
  "request_id": "req_rn_01J9R3...",
  "key_id": "key-3a6f...",
  "tenant_id": "tenant digest or opaque ID",
  "endpoint": "/v1/chat/completions",
  "protocol": "openai-chat-completions-v1",
  "model_alias": "ronin/default",
  "provider": "configured-provider-name",
  "stream": true,
  "message_count": 2,
  "tool_schema_count": 1,
  "input_token_count": 27,
  "output_token_count": 14,
  "token_count_kind": "native",
  "price_catalog_version": "2026-08-07",
  "reserved_cost_usd": 0.0032,
  "actual_cost_usd": 0.0019,
  "duration_ms": 842,
  "outcome": "completed",
  "http_status": 200
}
```

Forbidden fields include raw API keys, authorization headers, system prompts,
messages, completion text, tool names/arguments/results, provider credentials,
customer identifiers, IP addresses unless separately approved by privacy
policy, and exception stack traces. If correlation needs caller `user`, request
body, or route identity, Ronin stores a deployment-secret keyed digest rather
than the value.

## Required Tests When Implementation Begins

The implementation gate includes, at minimum:

| Test | Required assertion |
| --- | --- |
| Expired/revoked/malformed key | 401, no provider call, safe audit rejection. |
| Missing `gateway:chat` scope or model permission | 403, no provider call. |
| Malformed or unsupported request | 400/422, no reservation or provider call. |
| Rate, token, cost, and concurrency rejection | 429, atomic state unchanged except a safe rejection event. |
| Quota/audit store unavailable | 503, no provider call, no fallback to in-memory allowance. |
| Reservation race | Concurrent callers cannot cumulatively reserve beyond policy. |
| Provider failure before stream | Safe error, durable terminal audit, lease reconciled/retained. |
| Disconnect during stream | Upstream cancellation attempted, lease not prematurely released. |
| OpenAI and Anthropic wire examples | Contract-valid response and SSE framing, no Ronin-only body fields. |
| Audit redaction | Raw bodies, raw headers, raw key, tool arguments/results absent from every event. |

## Deferred Controls

These are intentionally outside the first hosted API release and require a
separate reviewed design: organization SSO, key IP allowlists, customer-managed
encryption, per-key content moderation policy, idempotency keys, usage export,
and outbound provider data-residency routing. Their absence does not relax the
gateway's default-deny quota or capability boundary.
