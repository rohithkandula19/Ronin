# Hosted Gateway API Contract

**Status:** Proposed for review. No hosted completion endpoint is implemented
by this document.

## Decision

Ronin's canonical public completion API will be **OpenAI Chat Completions
compatible**:

```
POST /v1/chat/completions
Authorization: Bearer rnk_live_...
```

The endpoint is optimized for this shape because it is the shared convention
across the OpenAI-compatible provider ecosystem, local-model servers, and many
editor/tool integrations. It also maps directly to Ronin's neutral message,
tool call, provider, and streaming abstractions. An OpenAI-compatible client
can switch its base URL and use a Ronin key without changing its request
format, for the supported field set.

Ronin will also expose an **Anthropic Messages compatibility adapter**:

```
POST /v1/messages
x-api-key: rnk_live_...
anthropic-version: 2023-06-01
```

It translates the supported Anthropic Messages subset into the same internal
request. It preserves the Messages endpoint, top-level `system` field,
content-block response format, and Server-Sent Events (SSE) framing. It is not
a claim of full parity with Anthropic beta, multimodal, container, prompt-cache,
or managed-tool features. Unsupported fields are rejected before a provider is
called; Ronin never silently drops them.

The underlying provider selection is configured by the Ronin deployment and
limited by the calling key's policy. A requested `model` is an allowed Ronin
model alias, such as `ronin/default` or a configured `anthropic:...` alias; it
is not an arbitrary remote provider credential or URL.

## Compatibility Matrix

| Concern | OpenAI Chat Completions (`/v1/chat/completions`) | Anthropic Messages (`/v1/messages`) | Ronin decision |
| --- | --- | --- | --- |
| Primary compatibility target | Yes | Adapter only | OpenAI first, because broad OpenAI-compatible clients can point at Ronin with a base-URL change. |
| Authentication | `Authorization: Bearer` | `x-api-key` and required `anthropic-version` | Both authenticate the same `rnk_live_...` key through the single Ronin key authority. |
| System prompt | `messages` role `system` or `developer` | top-level `system`; no `system` message role | Normalized into ordered system instructions. Conflicting system/developer instructions are retained in request order. |
| Text conversation | `messages` with roles | `messages` with `user` / `assistant` | Supported. Content is text only in the first implementation. |
| Client-defined function tools | `tools[].function` and `tool_choice` | `tools[]`, `input_schema`, and `tool_choice` | Supported as model-visible schemas. Ronin returns requested calls; it does not execute caller-defined tools. |
| Streaming | `chat.completion.chunk` SSE plus `[DONE]` | `message_start` through `message_stop` SSE events | Supported with format-specific event sequences. |
| Multimodal inputs, documents, computer use, web search, containers, prompt caching | Provider-specific | Provider-specific | Explicitly rejected with `unsupported_parameter` in this phase. |
| Provider reasoning controls | Provider-specific extensions | Provider-specific extensions | Not a public wire field in this phase. Key/deployment policy selects the normalized reasoning capability. |
| Response metadata | `chat.completion` / chunks | `message` / message events | Wire bodies contain only compatible fields. Ronin request correlation is carried in response headers, not undocumented body fields. |

The upstream references used for this mapping are the [OpenAI Chat Completions
reference](https://developers.openai.com/api/reference/resources/chat) and the
[Anthropic Create a Message reference](https://platform.claude.com/docs/en/api/messages/create).

## Shared Rules

Every supported endpoint has these rules:

1. The request body must be a JSON object and must be within the deployment's
   byte and message-count limits. Validation happens before quota reservation
   and provider invocation.
2. A valid, active `rnk_live_...` key must carry the `gateway:chat` scope and
   be allowed to use the requested model alias.
3. Ronin reserves rate, concurrency, token, and cost capacity before calling a
   provider. A reservation or quota-store failure is a denial, not a best-effort
   allowance.
4. External function definitions are descriptive only. The caller performs the
   requested function and sends the result in a later turn. This public
   completion API cannot access a Ronin checkout, shell, local files, MCP
   servers, or an operator's provider secrets.
5. Request bodies, prompt text, completion text, tool arguments, tool results,
   authorization headers, and raw keys are never written to the gateway audit
   event.
6. Unknown fields and recognized but unsupported fields receive a stable `422`
   response. They are not ignored or forwarded to the selected provider.

## Canonical OpenAI-Compatible Endpoint

### Request schema

`POST /v1/chat/completions`

| Field | Type | Required | Behavior |
| --- | --- | --- | --- |
| `model` | string | Yes | An allowed Ronin model alias. Unknown or disallowed aliases fail with `model_not_available`. |
| `messages` | non-empty array | Yes | Ordered text messages. Supported roles: `system`, `developer`, `user`, `assistant`, `tool`. |
| `messages[].content` | string or `null` for an assistant tool-call message | Yes, except assistant tool calls | Text only. Arrays, images, audio, and refusal blocks are rejected in this phase. |
| `messages[].tool_calls` | array | Required when an assistant message has `content: null` | OpenAI function-tool call objects are normalized to Ronin tool-call history. |
| `messages[].tool_call_id` | string | Required for role `tool` | Associates a tool result with a prior function call. |
| `max_tokens` | integer >= 1 | No | Maximum output tokens. Defaults to the deployment default and cannot exceed key or model policy. |
| `temperature` | number 0..2 | No | Forwarded only when the selected provider capability advertises temperature support; otherwise rejected. |
| `tools` | array | No | OpenAI `type: function` definitions only. Tool JSON Schema is validated and bounded. |
| `tool_choice` | `none`, `auto`, `required`, or function selector | No | Normalized to the internal tool-choice contract. |
| `stream` | boolean | No | `false` by default. `true` returns `text/event-stream`. |
| `stream_options.include_usage` | boolean | No | Supported only with `stream: true`; adds a terminal usage chunk before `[DONE]`. |
| `user` | string | No | An opaque caller correlation value. It is not an authorization principal and is stored only as a keyed audit digest. |

All other Chat Completions fields, including `n`, `logprobs`, `top_logprobs`,
`response_format`, `audio`, `prediction`, `service_tier`, and provider-specific
reasoning fields, are unsupported in the first hosted release and receive
`422 unsupported_parameter`.

### Non-streaming request and response

```http
POST /v1/chat/completions HTTP/1.1
Authorization: Bearer rnk_live_REDACTED
Content-Type: application/json

{
  "model": "ronin/default",
  "messages": [
    {"role": "system", "content": "Answer concisely."},
    {"role": "user", "content": "What does the test suite cover?"}
  ],
  "max_tokens": 128,
  "temperature": 0.2
}
```

```json
{
  "id": "chatcmpl_rn_01J9R3H9M2Z8",
  "object": "chat.completion",
  "created": 1786219200,
  "model": "ronin/default",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "The suite covers provider behavior, durability, CLI flows, and mission controls."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 27,
    "completion_tokens": 14,
    "total_tokens": 41
  }
}
```

The response includes `x-ronin-request-id` and `x-request-id` headers. It does
not add a Ronin-only response body field, preserving strict client decoders.
Usage uses the OpenAI names. When the selected provider has no authoritative
usage value, usage is reported from Ronin's documented token counter and the
audit metadata records whether it is `native`, `tokenizer`, or `estimated`.
The response body does not expose that internal label.

### Tool-call response

```json
{
  "id": "chatcmpl_rn_01J9R3T0P7Q1",
  "object": "chat.completion",
  "created": 1786219202,
  "model": "ronin/default",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_01J9R3T0",
            "type": "function",
            "function": {
              "name": "lookup_status",
              "arguments": "{\"service\":\"api\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {"prompt_tokens": 46, "completion_tokens": 15, "total_tokens": 61}
}
```

### Streaming response

```text
event: message
data: {"id":"chatcmpl_rn_01J9R3H9M2Z8","object":"chat.completion.chunk","created":1786219200,"model":"ronin/default","choices":[{"index":0,"delta":{"role":"assistant","content":"The suite "},"finish_reason":null}]}

event: message
data: {"id":"chatcmpl_rn_01J9R3H9M2Z8","object":"chat.completion.chunk","created":1786219200,"model":"ronin/default","choices":[{"index":0,"delta":{"content":"covers provider behavior."},"finish_reason":null}]}

event: message
data: {"id":"chatcmpl_rn_01J9R3H9M2Z8","object":"chat.completion.chunk","created":1786219200,"model":"ronin/default","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

When `stream_options.include_usage` is true, Ronin emits one final chunk with
an empty `choices` array and a `usage` object immediately before `[DONE]`.
Once a streaming response has started, a later provider or quota-settlement
failure cannot be represented as a normal HTTP JSON error. Ronin terminates the
stream and emits a safe `event: error` payload with an OpenAI-compatible error
object. The retained reservation remains conservative until durable
reconciliation succeeds.

## Anthropic Messages Adapter

### Request schema

`POST /v1/messages`

| Field | Type | Required | Behavior |
| --- | --- | --- | --- |
| `model` | string | Yes | Same allowed Ronin alias rules as the canonical endpoint. |
| `max_tokens` | integer >= 1 | Yes | Required by the Messages contract; bounded by key and model policy. |
| `messages` | non-empty array | Yes | `user` and `assistant` turns. Text strings and supported `text` / `tool_result` blocks only. |
| `system` | string or text-block array | No | Converted to normalized system instructions. `system` never enters `messages` as a role. |
| `tools` | array | No | Anthropic tool objects with `name`, optional `description`, and `input_schema`. |
| `tool_choice` | object | No | `auto`, `any`, `tool` choices normalized to the internal contract. |
| `stream` | boolean | No | `false` by default; `true` returns Anthropic-style SSE events. |
| `temperature` | number 0..1 | No | Same provider-capability rule as the canonical endpoint. |

Required headers are `x-api-key` and `anthropic-version`. Ronin accepts a
supported stable version value and returns `400 invalid_request_error` for a
missing or unsupported version. `anthropic-beta` is rejected in this first
release because beta contracts are not silently emulated.

### Non-streaming request and response

```http
POST /v1/messages HTTP/1.1
x-api-key: rnk_live_REDACTED
anthropic-version: 2023-06-01
Content-Type: application/json

{
  "model": "ronin/default",
  "max_tokens": 128,
  "system": "Answer concisely.",
  "messages": [
    {"role": "user", "content": "What does the test suite cover?"}
  ]
}
```

```json
{
  "id": "msg_rn_01J9R3H9M2Z8",
  "type": "message",
  "role": "assistant",
  "model": "ronin/default",
  "content": [
    {
      "type": "text",
      "text": "The suite covers provider behavior, durability, CLI flows, and mission controls."
    }
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {"input_tokens": 27, "output_tokens": 14}
}
```

`tool_use` content blocks map to the normalized tool call and produce
`stop_reason: "tool_use"`. Tool-result blocks in a later request are accepted
only when they reference a previous tool-use identifier in that supplied
conversation.

### Streaming response

```text
event: message_start
data: {"type":"message_start","message":{"id":"msg_rn_01J9R3H9M2Z8","type":"message","role":"assistant","model":"ronin/default","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":27,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"The suite covers provider behavior."}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":14}}

event: message_stop
data: {"type":"message_stop"}
```

## Error Contract

Ronin returns the dialect-appropriate error envelope and a safe request ID. No
error contains a key, prompt, completion, provider credential, provider URL,
stack trace, or quota-store detail.

OpenAI-compatible example:

```json
{
  "error": {
    "message": "API key budget cannot admit this request.",
    "type": "rate_limit_error",
    "param": null,
    "code": "budget_exceeded"
  }
}
```

Anthropic-compatible example:

```json
{
  "type": "error",
  "error": {
    "type": "authentication_error",
    "message": "Invalid API key."
  },
  "request_id": "req_rn_01J9R3..."
}
```

| Condition | HTTP | OpenAI `code` | Anthropic `error.type` |
| --- | --- | --- | --- |
| Missing, malformed, revoked, or expired key | 401 | `invalid_api_key` | `authentication_error` |
| Valid key lacks `gateway:chat` or model permission | 403 | `insufficient_scope` / `model_not_available` | `permission_error` |
| Body or supported-field validation fails | 400 or 422 | `invalid_request_error` / `unsupported_parameter` | `invalid_request_error` |
| Rate, concurrency, token, or cost reservation rejected | 429 | `rate_limit_exceeded` / `budget_exceeded` / `concurrency_limit_exceeded` | `rate_limit_error` |
| Durable quota or audit admission store unavailable | 503 | `quota_store_unavailable` | `api_error` |
| Selected provider unavailable after admission | 502 or 503 | `upstream_provider_error` | `api_error` / `overloaded_error` |

## Single Key System and Quota Admission

The gateway extends, rather than replaces, `ronin util api-keys`:

- Key format remains `rnk_live_...`; raw tokens remain visible only at creation.
- Key identifiers, salted digests, expiry, revocation, project/tenant binding,
  lifecycle audit, and `ApiKeyPolicy` remain owned by the existing key system.
- The CLI gains `gateway:chat` as an additional scope and gateway policy
  fields: allowed model aliases, maximum input tokens per request, maximum
  output tokens per request, requests per minute, maximum concurrency,
  cumulative tokens, and cumulative USD cost.
- `create`, `list`, `rotate`, `revoke`, and `audit` remain the only operator
  workflow. Hosted administration may call the same key-service interface; it
  must not mint a separate web-only key type.
- The current project-local JSON store remains valid for the existing local
  read-only gateway. A multi-process hosted deployment requires a transactional
  durable `ApiKeyStore` backend. It will refuse startup for hosted completions
  when that backend is not configured; a local JSON file is not silently used
  as a distributed quota authority.

Admission reserves an upper bound before provider invocation:

`reserved_tokens = counted_input_tokens + requested_or_policy_max_output_tokens`

`reserved_cost = price_bound(provider, model, reserved_tokens)`

The input count comes from Ronin's provider-aware counter. `native` counts are
used where available, `tokenizer` counts for configured local tokenizers, and
the documented UTF-8 fallback is **estimated**, not a provider guarantee. A
cost-bound calculation requires an active, versioned price entry for the chosen
provider/model. If a key has a cost budget and Ronin lacks that price entry,
admission is denied rather than guessed. Settlement records actual usage and
releases only the unused reservation.

The detailed failure protocol and key-holder threat model are in
`docs/security/hosted-gateway-threat-model.md`.
