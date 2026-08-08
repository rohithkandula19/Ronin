# The provider layer

> Swapping Claude for Kimi K2 or a local Qwen is a config change, not a code change.

That sentence is the whole design goal, and everything below is either how it is
achieved or where it is honestly incomplete.

```
uv run python -m ronin.providers.demo     # all of it running, offline, no keys
```

---

## 0. The shape

```
        ┌───────────────────────────────────────────────────────────┐
        │ ronin.core.loop.run_turn   (knows nothing about providers) │
        └────────────────────────────┬──────────────────────────────┘
                                     │ core.protocols.ModelClient
                          ┌──────────▼──────────┐
                          │ bridge.LoopClient   │  translation, ~40 lines
                          └──────────┬──────────┘
                                     │ providers.base.ModelClient
   ┌─────────────────────────────────▼─────────────────────────────────┐
   │ router      role → model            registry   provider → adapter │
   │ assembly    stable prefix + cache   accounting tokens + cost      │
   └─────────────────────────────────┬─────────────────────────────────┘
                                     │
     ┌───────────────┬───────────────┼───────────────┬────────────────┐
     ▼               ▼               ▼               ▼                ▼
  anthropic    openai_compat     moonshot       mlx_local      shim (wraps any
  native       8 hosts, one      Kimi K2        in-process     client whose model
  tools        base_url          quirks         Apple silicon  lacks native tools)
     └───────────────┴───────────────┴───────────────┘
                          normalize + jsonargs
                    one ToolUse(id, name, input: dict)
                                     │
                                 Transport
                    (httpx in production, a file in tests)
```

Two rules hold the shape together:

- **`Capabilities`, not model names.** Code that branches on `model == "claude-…"`
  breaks the moment someone points `base_url` at a proxy. Code that branches on
  `caps.native_tools` keeps working, because that is the property it cares about.
- **`Transport` is injected.** An adapter that calls `httpx` directly can only be
  tested against a live endpoint. Every adapter is handed something that yields
  bytes, and in tests that something reads a file — which is why 309 tests run
  offline and why the golden fixtures are possible at all.

---

## 1. Capabilities

```python
Capabilities(native_tools, parallel_tools, prompt_cache, thinking, max_context, vision)
```

Every field only ever *removes* work; none is required for the layer to function, or
a new provider becomes a code change again. Two invariants are enforced in the type:

- `parallel_tools` requires `native_tools` — the format shim is strictly sequential,
  and claiming parallel dispatch on top of it would tell the loop it may reorder
  calls it must not reorder.
- `max_context > 0`.

`CONSERVATIVE` is what we assume when a provider says nothing: the narrow end of
every axis, because a wrong "can't do that" costs a slower path and a wrong "can"
costs a failed request the user has to debug.

---

## 2. The tool-call normalizer

Every provider gets tool calls wrong differently. `normalize.py` is the single place
that knows about all of it, so no adapter carries repair logic and every adapter
produces byte-identical `ToolUse` values for equivalent input.

| Shape | Who does it | What we do |
|---|---|---|
| Args stream as JSON fragments | everyone | buffer per index, parse once at the end |
| Args as a string, not an object | the OpenAI wire format | `jsonargs.parse_arguments` |
| Markdown-fenced args | weak/local models | `stripped_fence` |
| Trailing commas | weak/local models | string-aware comma removal |
| Python literals (`None`/`True`) | Python-heavy fine-tunes | token-level rewrite outside strings |
| Double-encoded args | Kimi, some gateways | unwrap once, record `double_encoded` |
| Truncated mid-stream | dropped connections | close open strings/brackets, record `truncated` |
| **Missing ids** | Ollama, llama.cpp, fine-tunes | mint one **deterministically** from content |
| **Colliding ids** | servers reusing `call_0`; Kimi's `functions.NAME:0` | disambiguate to `id#2` |
| Several calls in one text block | local models | each balanced object read separately |
| Name streamed in pieces | Kimi, long tool names | merge without duplicating |
| Index absent on continuations | Kimi | continue the last index seen |

Three decisions in there are worth stating outright:

**Minted ids are content-derived, not random.** A uuid would make the golden fixtures
untestable and would change on replay. `synthetic_id` hashes `(index, name, args)`,
and the batch pass afterwards resolves the one case content-derivation cannot —
two genuinely identical calls in one response.

**Colliding ids are a bug that surfaces a turn late.** Two `tool_use` blocks sharing
an id make `tool_use` ↔ `tool_result` pairing ambiguous, which providers reject with
a 400 on the *next* request — far from the cause. We rename at the source.

**A call we cannot parse is kept, not dropped.** Dropping it makes the model look
like it said nothing, so it retries the same broken call forever. It comes back as
`NormalizedCalls.failed` with the raw payload, and the caller decides.

Every repair is *recorded*. A tool that ran on guessed arguments is worse than a
tool that refused, so `Completed.notes` always says what had to be fixed.

---

## 3. The format shim

When `capabilities().native_tools` is False we stop asking the API for tool calls and
start asking the model:

```
<ronin:tools>
  <ronin:tool>
    <name>read_file</name>
    <description>Read a file from disk.</description>
    <parameters>{"properties": {"path": {"type": "string"}}, "type": "object"}</parameters>
  </ronin:tool>
</ronin:tools>

<ronin:tool_call_format>
To call a tool, emit exactly this, and nothing else on those lines:
<ronin:tool_call>{"name": "TOOL_NAME", "arguments": {"ARG": "VALUE"}}</ronin:tool_call>
…
</ronin:tool_call_format>
```

The tag is namespaced because bare `<tool_call>` collides with the chat template of
several fine-tunes, which emit it themselves and would make every response look like
a call.

### Why it is a state machine and not a regex

The tag arrives split across chunks — `"<ronin:to"` then `"ol_call>"`. A regex over
the finished string works only once you *have* the finished string, by which point
the fragment is already on the user's terminal. `ShimStreamParser` withholds the
longest suffix that could still become a tag and releases it the moment it cannot:

```
"if a < b"          → holds "<", releases it when "b" proves it is not a tag
"…<ronin:to"        → holds 10 characters
"…<ronin:tool_other>" → releases all of it, it was prose after all
```

An unterminated block becomes a *failure*, never visible text: leaking half a JSON
object into the answer is worse than reporting that the model got cut off.

### The repair loop

A bad call is quoted back verbatim with the format restated — from the same constant
the system prompt used, so the two cannot drift — up to **2** repairs. Two is not
arbitrary: one retry catches a formatting slip, a second catches a model that needed
the format restated. A third has never helped, and an agent silently retrying a
malformed call is indistinguishable from a hang.

On exhaustion the turn ends with `finish=ERROR` and notes explaining, so the loop
does not read "no tool calls" as a finished answer. The stable prefix is byte-identical
across a repair, so a bad call does not also cost the whole cached prefix.

---

## 4. The router

```toml
[roles]
main = "kimi-k2"        # writes the code, drives the turn
plan = "deepseek-r1"    # thinks before acting
fast = "qwen-local"     # search triage, summarizing, compaction
```

**Never burn the main model on grep triage.** A subagent summarizing forty search
hits and a compaction pass rewriting history are both mechanical work, and routing
them to the model that writes the code is how a session costs ten times what it
should while getting no better.

So `Router.for_subagent()` and `Router.for_compaction()` **take no role argument** —
they cannot be called wrong by forgetting one. A config that omits `fast` falls back
to `main` rather than crashing mid-session; the fallback shows up in the ledger as a
`fast` row naming the main model, which is the signal to fix the config.

Three roles, deliberately. A fourth is a fourth thing to configure and a fourth
chance to route expensive work to an expensive model by accident.

Config holds `api_key_env` — the *name* of an environment variable — never a key. A
config file with a secret in it ends up in a git history.

See [`examples/models.toml`](../examples/models.toml) for a full annotated config,
including a fully-free all-local setup.

---

## 5. Cache-aware assembly

Prompt caching is a prefix match: the provider caches the longest leading span it has
seen before, and the match ends at the first byte that differs. The expensive part is
not enabling it — it is never reordering the front of the prompt.

So the stable prefix is a value, rendered in one fixed order, front to back by how
often each part changes:

1. **system prompt** — changes when the product changes
2. **tool specs** — changes when the toolset changes, per session
3. **repo map** — changes when the repo changes, per commit

Anything that changes per *turn* is a message, and messages come after the marker.
`assemble()` is the only way to build a request from a prefix, and `extend()` the only
way to add turns — it does not have the pieces to reorder anything.

`StablePrefix.fingerprint()` makes a broken promise visible. Log it per request; a
changed value on a turn that should have been cacheable *is* the bug. Tool order is
part of the identity on purpose, because reordering the list breaks the cache.

Anthropic's `cache_control` is a per-block marker, so `cache_marker` names the last
stable block and the adapter places it. When tools are present the marker goes on the
last *tool*, not the system prompt — tool definitions sit after the system prompt in
the prefix, so marking the system block would leave the schemas outside the cached
span and re-bill them every turn.

### Measuring it

`CacheStats` measures in **tokens, not requests**. A 40-token miss is nothing like a
30k-token miss, and a per-request rate calls them the same thing. It also reports
`write_only_prefixes` — prefixes seen exactly once, written to the cache and never
read back, which is the commonest way caching costs money instead of saving it.

---

## 6. Accounting

`Ledger` is sqlite (stdlib, real aggregation, survives a crash) keyed on
`(session_id, request_id)` so a retried write updates rather than double-counts.

Two distinctions keep the numbers trustworthy:

- **Unpriced is not free.** A model with no price records `cost_usd = 0` *and*
  `priced = 0`, so a total says "+20 unpriced" instead of implying a $0 session.
- **Unreported is not zero.** A provider that sends no usage produces a row with
  `reported = 0`. "Used no tokens" versus "did not say" is the difference between a
  working budget and a broken one.

`role_totals()` is where a routing mistake becomes visible: a `main` row with twenty
requests and small token counts is grep triage on the expensive model.

Time is injected (`clock`), so ledger tests are deterministic.

---

## 7. Provider notes

### anthropic
Tool args stream as `input_json_delta`, not as an `arguments` string. Content-block
indices are mapped to tool-call ordinals (a call at content index 2 is call 1, not 2).
Thinking blocks carry a `signature` that must be replayed verbatim or the continuation
is rejected — it rides on `Thinking.signature` and is never edited.

`message_delta` reports the **running total** of output tokens, not an increment.
Adding it to the `message_start` value bills the first token twice on every request —
small per call, and wrong in the direction that makes a budget stop early.

### openai-compatible
One adapter for OpenAI, DeepSeek, Together, Groq, OpenRouter, vLLM, llama.cpp's
server, LM Studio and Ollama. They differ in `base_url` and in which optional fields
come back — none of which justifies eight adapters. A *different wire protocol* does,
which is why Anthropic and MLX have their own.

`prompt_tokens` here **includes** cached reads, unlike Anthropic which reports them
apart. We subtract, so `input_tokens` means "billed at the full rate" for every
provider — which is what the ledger's pricing assumes.

Also handled: `reasoning_content` (DeepSeek-R1 and distills) surfaced as thinking not
as the answer, and backends that answer a `stream=true` request with one whole
`message` object.

### moonshot / kimi k2
A subclass with three overrides, not a second adapter:

1. **Ids are unreliable** — absent, reused across calls, or shaped
   `functions.read_file:0`, which is a *name* with an index glued on. That string is
   identical for every call to that tool, so two parallel reads collide. Ids matching
   that shape are discarded and a stable one is minted.
2. **`finish_reason` disagrees with itself** — a response with tool calls may report
   `stop`. We trust the presence of calls over the label.
3. **Args are occasionally double-encoded** — unwrapped, with the repair recorded.

### mlx (local)
No HTTP at all — drives `mlx_lm` in process, which is the point: a server costs a
subprocess, a port, and a second copy of the weights in RAM on a machine where RAM
is the binding constraint. `mlx_lm` is imported inside the method so `import
ronin.providers` stays free on Linux and in CI.

Generation is blocking, so tokens cross into async through `asyncio.to_thread` and a
queue — a tight `for` loop would starve the event loop and make interrupt handling a
lie. Usage is reported as **unknown**, not zero: estimating tokens here would put a
made-up number in the ledger.

---

## 8. Tests

309 tests, no network anywhere.

**Golden fixtures** (`tests/providers/fixtures/`) are real wire bytes on disk —
three provider families plus local token streams, with **three deliberately malformed
payloads per provider**. Every SSE fixture is replayed under six chunkings including
**one byte at a time**, and the normalized output is required to be identical across
all of them. A result that depends on how the network happened to fragment a response
is a bug, and that is where it shows up.

The headline assertion is `test_every_provider_normalizes_the_same_call_identically`:
the same logical response, as each provider actually puts it on the wire, must produce
the same `ToolUse`. If that passes, the claim at the top of this document is true.

The integration test (`test_bridge.py`) drives the *real* `ronin.core.loop.run_turn`
over a real `AnthropicClient` reading recorded bytes, across two model turns, and
asserts the resulting `AgentState` has no unpaired tool calls.

---

## 9. Honest status

**The two `ModelClient` protocols are not one protocol.** `ronin.core.protocols.ModelClient`
takes `(system, messages, tools)`; this package's takes a `ModelRequest`. Both are
deliberate — the loop's seam is narrow so it need not change when a provider does, and
the provider seam needs a request object so the cacheable prefix, cache marker and
sampling knobs travel together. `bridge.LoopClient` is the translation, about forty
lines. **This is a decision worth your review**: the alternative is changing
`core.protocols` and the loop's 93 tests to carry fields the loop does not use. The
bridge is deliberately trivial so reversing it later is cheap.

**Not built here, and not pretended:**

- No retry/backoff on transient statuses. `HttpTransport` classifies them
  (`ProviderError.retryable`) but nothing acts on it yet. The legacy
  `packages/agent-patterns` provider has a real retry ladder that should move over.
- No failover between providers. Same story — `packages/agent-patterns/providers/failover.py`
  exists and is not wired here.
- `vision` is declared in `Capabilities` and no adapter renders an image block yet.
  It is a promise about a model, not a feature.
- The legacy `packages/agent-patterns/providers/` tree still exists and is what the
  shipped CLI uses. This is the v2 tree; nothing imports across the boundary in
  either direction, and the migration is not done.
- `packages/agent-patterns/providers/text_tools.py` is the prior art for the shim, and
  it is a regex over the finished string — which is exactly the thing the work order
  called out. It is still in use by the shipped CLI.
