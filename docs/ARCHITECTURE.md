# Ronin architecture — the contract

Ronin is a terminal-native, provider-agnostic coding agent. This document is the
contract the whole system is built on: the data types, the turn state machine,
the package boundaries, and the event bus. It is written **before** the
implementation so the boundaries are decided rather than discovered.

The types are real code, not sketches: `src/ronin/core/types.py`, with 100%
statement and branch coverage of their invariants in `tests/core/test_types.py`.
That module contains **no logic** — frozen dataclasses, enums, and one
transition table.

Scope note: this contract describes the `ronin.core` v2 architecture. The shipped
`packages/*` tree predates it; §6 maps the two honestly rather than pretending
they already agree.

---

## 0. The shape of the system

```
┌─ entry ────────────────────────────────────────────────┐
│ tui  ·  headless (-p)  ·  python sdk  ·  ide/lsp       │
└────────────────────────┬───────────────────────────────┘
┌─ orchestrator ─────────▼───────────────────────────────┐
│ session · turn state machine · interrupt · resume      │
└────────────────────────┬───────────────────────────────┘
┌─ agent loop ───────────▼───────────────────────────────┐
│ ReAct: plan → tool call → observe → reflect → repeat   │
│ todo state · budget guard · stall detector             │
└──────┬──────────────────────────────┬──────────────────┘
       │                              │
┌─ model layer ─────────┐   ┌─ tool layer ───────────────┐
│ provider adapters     │   │ read write edit multiedit  │
│ tool-call normalizer  │   │ bash(persistent) glob grep │
│ format shim (xml/json)│   │ web task todo mcp          │
│ router · cache · cost │   └──────────┬─────────────────┘
└───────────────────────┘              │
┌─ context engine ──────┐   ┌─ safety ─▼─────────────────┐
│ repo map · retrieval  │   │ permissions · sandbox      │
│ compaction · RONIN.md │   │ injection scan · diff gate │
└───────────────────────┘   └────────────────────────────┘
┌─ verification ────────┐   ┌─ persistence ──────────────┐
│ test runner · lint    │   │ transcripts · checkpoints  │
│ self-critique · retry │   │ git shadow · undo          │
│                       │   │ sqlite index · fts5 search │
└───────────────────────┘   └────────────────────────────┘
┌─ flywheel ─────────────────────────────────────────────┐
│ eval suite → failure mining → LoRA adapter → re-eval   │
└────────────────────────────────────────────────────────┘
```

Two rules make this diagram load-bearing rather than decorative: the model layer
and the tool layer never reference each other (§3), and every arrow *up* from the
loop is an `Event` (§4).

---

## 1. Core data types

All types are `@dataclass(frozen=True, slots=True)`. Frozen is a design choice,
not a style: the loop advances state by producing a new value, so a checkpoint is
an immutable value, "resume" is just "use that value", and a consumer can never
mutate state behind the loop's back.

### 1.1 Content blocks and messages

```python
Text(text)                                  # prose
Thinking(text, signature="")                # reasoning; signature is provider-opaque
ToolUse(id, name, arguments={})              # the model's request to call a tool
ToolResultBlock(tool_use_id, content, is_error=False)   # the answer, in transcript form

ContentBlock = Text | ToolUse | ToolResultBlock | Thinking     # closed union

Message(role: Role, content_blocks: tuple[ContentBlock, ...], metadata: Mapping)
```

Enforced invariants:

| Invariant | Why |
|---|---|
| `content_blocks` is **always** a tuple of blocks, never a bare string | A string cannot express a tool call; supporting both shapes is how pairing bugs get in. Passing `"hello"` raises. |
| `ToolUse` and `Thinking` may appear **only** on an assistant message | A user or tool message carrying a tool call is a malformed transcript. |
| `ToolUse.id` and `.name` are non-empty | Duplicate/blank tool-call ids have caused real provider 400s. |
| `ToolUse.arguments` is a `Mapping`, never a JSON string | Normalizing a provider's stringly-typed arguments is the model layer's job; the contract refuses the ambiguity. |
| `Message.text` excludes `Thinking` | Reasoning is not the answer. Anything rendering thinking as the reply is a bug. |

**The pairing invariant** is exposed as a pure function:

```python
unpaired_tool_uses(messages) -> tuple[str, ...]   # ids with no answering result
```

Every `ToolUse` must be answered by exactly one `ToolResultBlock` with the same
id. Providers reject an unpaired transcript, so **compaction, truncation, and
interrupt handling must all be checked against this function** — it is the one
rule most easily broken by an optimization.

### 1.2 Tools

```python
class DangerLevel(int, Enum):        # ordered: comparisons are meaningful
    READ_ONLY = 0
    MUTATING = 1
    DESTRUCTIVE = 2
    IRREVERSIBLE = 3

ToolSpec(name, description, json_schema={}, danger_level=READ_ONLY, requires_approval=False)
ToolResult(ok, content="", error="", artifacts=(), tokens_estimate=0)
```

`ToolSpec` carries **no handler**. A spec is data — describable, serializable,
safe to send to a model — while the callable lives in the tool layer's registry.
That split is what lets the loop reason about a tool without importing it.

Enforced invariants:

- `description` is required: it is the only thing the model has to decide
  whether to call the tool.
- **A `DESTRUCTIVE` or `IRREVERSIBLE` tool cannot declare
  `requires_approval=False`.** Danger and approval are separate fields — one
  describes the world, the other describes policy — but the dangerous end of the
  scale may not opt out of the gate at declaration time.
- `ToolResult` uses one shape for success and failure. `ok=False` with a
  populated `error` is a **normal** outcome fed back as an observation; tools do
  not raise to signal task failure. `ok=True` with an error, or `ok=False`
  without one, both raise — a silent failure gives the model nothing to recover
  from.
- `as_block(tool_use_id)` projects a result into the transcript, preserving the
  error flag, so the pairing rule is satisfied by construction.

### 1.3 Agent state

```python
Todo(id, subject, status=PENDING)
Budget(max_tokens=None, max_usd=None, max_wall_seconds=None,
       spent_tokens=0, spent_usd=0.0, elapsed_seconds=0.0)

class Mode(str, Enum):               # a permissiveness ladder, not a bag of flags
    PLAN = "plan"; ASK = "ask"; AUTO_EDIT = "auto_edit"; FULL = "full"

AgentState(messages=(), todos=(), cwd=".", budget=Budget(), mode=ASK, checkpoint_id=None)
```

`AgentState` is *everything needed to resume, and nothing else*. Enforced:
collections are tuples; todo ids are unique; `cwd` is non-empty;
`checkpoint_id` is `None` or meaningful (never `""`). `state.pairing_errors`
surfaces the §1.1 invariant, and `with_message()` returns a new state.

`Mode` is a ladder (`mode.at_least(Mode.AUTO_EDIT)`) so "more permissive than X"
is expressible — the alternative, independent booleans, cannot express it and
invites a permissive mode that silently skips a check.

`Budget` separates limits from spend so a budget can be *checked* without being
mutated, and `exhausted` is a pure property over both.

### 1.4 Events

```python
TurnStart(turn_index, state=THINKING)
TextDelta(text, thinking=False)
StreamReset(reason="")                       # discard rendered text; re-streamed
ToolStart(tool_use_id, name, arguments={})
ToolEnd(tool_use_id, name, result: ToolResult)
ApprovalRequest(tool_use_id, name, danger_level, rendered, reason="")
Compaction(folded_messages, token_estimate_before=0, token_estimate_after=0,
           reason="", summarizer_failed=False)
VerifyResult(ran, passed=False, checks_passed=0, checks_failed=0,
             summary="", repaired=False)
TurnEnd(turn_index, state, stop_reason="")
Error(message, kind="unknown", recoverable=False)

Event = (TurnStart | TextDelta | StreamReset | ToolStart | ToolEnd
         | ApprovalRequest | Compaction | VerifyResult | TurnEnd | Error)
```

- `TextDelta.thinking` marks reasoning **in the event**, so a renderer never has
  to guess which stream it is looking at.
- `ApprovalRequest.rendered` is required and carries the exact command or diff
  the human is deciding on. **What is shown is what runs** — a consumer must not
  re-derive it. (Gemini CLI shipped a UI-spoofing bug where whitespace padding
  pushed the malicious tail of a command out of the visible prompt; putting the
  rendered text in the event closes that class.)
- `TurnEnd.state` must be `DONE`, `ERROR`, or `INTERRUPTED`. A turn cannot end
  mid-flight.
- **`Compaction` and `VerifyResult` carry plain scalars, not the richer result
  types from `context/` and `verify/`.** Referencing
  `ronin.context.compaction.CompactionResult` here would make `core` import
  `context` and invert §3's dependency rule — the one that lets this contract be
  tested with no provider, no shell and no context engine. A consumer that needs
  more than the numbers reads the result object from the layer that produced it.
- `Compaction` carries **both** token estimates because the ratio is the only
  honest measure of whether compaction bought anything; a single "after" number
  cannot express it. `token_estimate_after > token_estimate_before` is rejected —
  a summarizer that grew the transcript is a bug, not a nuance to render.
- **`VerifyResult.ran` is separate from `passed`.** An unverifiable change (no test
  command, no language rules) is neither pass nor fail, and collapsing the two
  would report a missing test suite as a broken change. `repaired` says the loop
  fixed something; `passed` says it is now correct. They are independent.
- Neither event is surfaced by the TUI yet: `ViewState` has no field for them, and
  `ui/reduce.py` ignores them **explicitly**, with the reason in a comment.
  `ui/headless.py` emits both losslessly as JSON, so scripts lose nothing. Adding a
  TUI surface is a deliberate change, not a gap to fill silently.

---

## 2. The turn state machine

```
IDLE ──▶ THINKING ──▶ TOOL_PENDING ──▶ AWAITING_APPROVAL ──▶ TOOL_RUNNING ──▶ OBSERVING
           │  │            │                  │  │                                │  │
           │  │            └──────────────────┼──┴────────▶ (no approval needed)   │  │
           │  │                               └───────────▶ OBSERVING (denied)     │  │
           │  └──▶ VERIFYING ──▶ DONE ──▶ IDLE                                     │  │
           └────────────────────────────────────◀───────── THINKING ◀───────────────┘  │
                                                            VERIFYING ◀────────────────┘
   any active state ──▶ ERROR | INTERRUPTED ──▶ (THINKING | DONE | IDLE)
```

The table is data, in `TRANSITIONS`. Anything absent from it is a bug, not a
nuance, and `assert_transition()` raises `IllegalTransition` naming the legal
targets.

| From | Legal targets |
|---|---|
| `IDLE` | `THINKING` |
| `THINKING` | `TOOL_PENDING`, `VERIFYING`, `DONE`, `ERROR`, `INTERRUPTED` |
| `TOOL_PENDING` | `AWAITING_APPROVAL`, `TOOL_RUNNING`, `ERROR`, `INTERRUPTED` |
| `AWAITING_APPROVAL` | `TOOL_RUNNING`, `OBSERVING`, `ERROR`, `INTERRUPTED` |
| `TOOL_RUNNING` | `OBSERVING`, `ERROR`, `INTERRUPTED` |
| `OBSERVING` | `THINKING`, `VERIFYING`, `DONE`, `ERROR`, `INTERRUPTED` |
| `VERIFYING` | `THINKING`, `DONE`, `ERROR`, `INTERRUPTED` |
| `DONE` | `IDLE` |
| `ERROR` | `THINKING`, `DONE`, `IDLE` |
| `INTERRUPTED` | `THINKING`, `DONE`, `IDLE` |

Deliberate choices worth arguing about:

- **`TOOL_PENDING → TOOL_RUNNING` skips approval** when the spec requires none.
  Approval is a state, not a step every call pays for.
- **`AWAITING_APPROVAL → OBSERVING`** is the *denial* path: a refused tool still
  produces an observation ("the user declined"), because the model must learn
  what happened rather than silently retry.
- **`ERROR` and `INTERRUPTED` are terminal-ish, not leaves.** They end a turn but
  resume into `THINKING` (retry/continue), settle into `DONE`, or abandon to
  `IDLE`. That is what makes Ctrl-C recoverable instead of fatal.
- **No state transitions to itself** — a self-loop would hide progress from the
  stall detector. Tested.
- **`RESTING_STATES = {IDLE, DONE, AWAITING_APPROVAL}`** are the only states a
  turn may sit in indefinitely. Anything else sitting still means the loop is
  wedged, which is the stall detector's definition.

---

## 3. Package boundaries and the dependency graph

```
                          ┌──────────────┐
                          │  core/types  │  imports nothing from ronin
                          └──────▲───────┘
    ┌───────────┬───────────┬────┴───┬───────────┬───────────┬──────────┐
┌───┴────┐ ┌────┴───┐ ┌─────┴──┐ ┌───┴────┐ ┌────┴─────┐ ┌───┴───┐ ┌────┴───┐
│context/│ │safety/ │ │verify/ │ │persist/│ │providers/│ │tools/ │ │  ui/   │
└───┬────┘ └────┬───┘ └─────┬──┘ └───┬────┘ └────┬─────┘ └───┬─┬─┘ └────┬───┘
    │           │           │        │           │           │ │        │
    │           │           │        │           │      ┌────┴─┴───┐    │
    │           │           │        │           │      │ agents/  │    │
    │           │           │        │           │      │  mcp/    │    │
    │           │           │        │           │      └────┬─────┘    │
    └───────────┴───────────┴────────┴─────┬─────┴───────────┘──────────┘
                                           │
                                  ┌────────┴────────┐
                                  │  core/loop      │  providers + tools,
                                  └────────▲────────┘  as injected protocols
                                  ┌────────┴────────┐
                                  │  session.py     │  the orchestrator seat
                                  └────────▲────────┘
                                  ┌────────┴────────┐
                                  │     cli/        │  app, SDK, headless
                                  └─────────────────┘
```

**The rules, stated as prohibitions:**

| Rule | Rationale |
|---|---|
| `core/` imports **nothing** from `ronin.*` | It is the shared vocabulary; a dependency here would make every other rule unenforceable. |
| **nothing in `tools/` may import from `providers/`** | A tool that knows which model is calling it will special-case for one. Tools take arguments and return a `ToolResult`, full stop. |
| **nothing in `providers/` may import from `tools/`** | An adapter that imports tools ends up executing them, which is how "the provider layer" quietly becomes a second agent loop. Adapters receive `ToolSpec` *data*. |
| `core/loop` takes both as **injected protocols** | It is the only place that needs both halves, and it gets them without importing either — which is what lets it be tested with a scripted fake and no network. |
| `context/`, `safety/`, `verify/`, `persistence/`, `ui/` depend only on `core/` | They must be testable against types, not against a live model, a live shell, or a live filesystem. Each takes what it needs — a summarizer, a subprocess runner, an event stream — as an injected callable. |
| `agents/` and `mcp/` may import `core/` **and** `tools/`, never `providers/` | Both *produce* tools, so they sit above the tool layer. Neither may learn which model is calling. |
| `session.py` imports all three layers; nothing else does | It exists to introduce them. §0's orchestrator seat. |
| `cli/` may import anything | It is the application. Everything below it is a library. |
| **nothing below `cli/` may import `cli/`** | The inverse of §4: the loop emits `Event`s and never knows who is rendering them. |

Every row above is a test. `tests/tools/test_boundaries.py` holds the table as
`LAYER_RULES` and walks the import graph with `ast`, so the prohibitions fail CI
rather than needing a reviewer to notice. One further test asserts the table
*covers* every package on disk — an allowlist that silently stops covering a new
directory is the failure mode of every allowlist ever written.

`ToolSpec` carrying no handler (§1.2) is what makes the `providers/`↮`tools/`
prohibition *natural* rather than aspirational: the provider layer needs tool
**descriptions**, and descriptions are core types.

**These rules are now a gate, not documentation.** `tests/tools/test_boundaries.py`
walks the import graph with `ast` and fails on a violation. It parses rather than
imports, so a lazy `from ronin.providers... import` *inside a function* — the exact
place a boundary quietly dissolves, deferred to "avoid the cycle" — is caught too.

Two things are exempt. `ronin/session.py`, the orchestrator seat, exists to import
all three layers and introduce them; the test asserts *both* halves of that —
nothing else imports all three, **and** `session.py` still does. If the wiring ever
migrates somewhere it should not be, the second assertion is what notices. And the
whole of `ronin/cli/`, stated as a package rather than module by module: the
enumerated form was worse than useless, because a new `cli` module was unconstrained
until someone remembered to list it and the only signal was a failure in an
unrelated test.

---

## 4. The event bus contract

**The loop is a generator of `Event`s. The TUI, the headless runner, and the SDK
are all just consumers.**

```python
def run_turn(state: AgentState, ...) -> Generator[Event, ApprovalDecision | None, None]: ...

# every entry point is the same shape
decision = None
generator = run_turn(state)
while True:
    try:
        event = generator.send(decision)
    except StopIteration:
        break
    decision = None
    match event:
        case TextDelta(text=text):        render(text)
        case ToolStart(name=name):        spinner(name)
        case ApprovalRequest() as req:    decision = ask_human(req)   # the only reply
        ...
```

**Superseded — the injected policy answers approvals.** The loop shipped in
`src/ronin/core/loop.py` is a plain `AsyncIterator[Event]`, and approval is
answered by the injected `Policy` (`await policy.approve(spec, use, rendered=…)`),
not sent back through the stream. Two consequences worth being explicit about:

- The event stream stays strictly **one-way**, so recording it, replaying it, and
  fanning it out to several consumers are all trivial.
- `ApprovalRequest` is **informational**: a UI renders it to show what is being
  decided, but the decision does not travel back over the stream.
- `ApprovalDecision` is still the value that answers — it is just returned by the
  policy rather than sent by the consumer. `DENY_UNATTENDED` remains the correct
  answer for a policy with no human attached.

The `asend()` design below is what §4 originally specified; it is kept as the
rejected alternative because the tradeoff is real (a `Policy` that prompts a human
mixes decision-making with UI, which is the cost of the shipped choice).

The contract:

1. **No UI code inside the loop, ever.** No `print`, no `rich`, no cursor
   control, no colour, no progress bar, no `input()`. If the loop needs something
   rendered, it emits an event describing it.
2. **Events are values** — frozen, serializable, no callbacks or live objects
   inside. That is what lets the same stream drive a terminal, a JSON-lines
   headless run, an SDK caller, and a replay from a transcript.
3. **Consumers may not mutate state.** They receive `Event`s; they do not touch
   `AgentState`. A consumer that needs to change the run sends an input, it does
   not reach in.
4. **Approval is the one place the loop waits on a human.** It emits
   `ApprovalRequest` and expects an `ApprovalDecision` back. Two consequences: a
   consumer with no human attached must answer *deny* — `DENY_UNATTENDED` exists
   precisely so unattended runs cannot hang or auto-allow — and the decision is
   made against `rendered`, the exact text shown.
5. **`Error` is an event, not an exception**, for anything a consumer should
   display. Exceptions crossing the generator boundary are bugs; `recoverable`
   decides whether the turn may resume.
6. **Ordering is guaranteed**: exactly one `TurnStart` first, exactly one
   `TurnEnd` last, and every `ToolStart` is followed by a matching `ToolEnd`
   (with the same `tool_use_id`) unless the turn ends in `ERROR`/`INTERRUPTED`
   first.

Why a generator and not callbacks: callbacks invert control, so the UI ends up
driving the loop and back-pressure is impossible. A generator lets the consumer
choose when to pull, makes "record the stream and replay it" free, and makes the
headless runner and the SDK the *same* code path as the TUI rather than a second
implementation that drifts.

---

## 5. What this contract deliberately does not decide

Stated so silence isn't mistaken for a decision: prompt assembly and compaction
strategy; the retrieval/repo-map algorithm; which providers exist and how their
quirks are normalized; the sandbox mechanism (Ronin has **no** OS-level sandbox
on the main path today — see `docs/design/policy-engine-design.md`); how
checkpoints are stored; and the concrete permission rule language.

---

## 6. Relationship to the shipped `packages/*` tree — honest mapping

The shipped code predates this contract and does **not** yet conform to it. The
differences are real and worth naming rather than papering over:

| This contract | Shipped today |
|---|---|
| `Message.content_blocks: tuple[ContentBlock, ...]` | the provider-layer `Message` carries content differently; block-typed content is new here |
| `ToolSpec` (data, no handler) | `ronin_agent_patterns.types.Tool` bundles the handler with the declaration |
| `ToolResult(ok, content, error, artifacts, tokens_estimate)` | tool handlers largely return strings; there is no structured result type |
| `DangerLevel` (4 ordered levels) | `sensitive=True` + a hard-coded `SENSITIVE_TOOLS` set + separate plugin capabilities + pack `risk_level` — several disjoint vocabularies |
| `AgentState` (frozen, resumable) | state is spread across the loop, session store, and `RunJournal` checkpoints |
| Loop **yields** `Event`s | the loop takes **callbacks** (`on_text`, `before_tool`, `on_retry`, `on_reset`); `before_tool` is bidirectional, which is why §4 uses a `Generator` |
| `Message.role` includes `SYSTEM` | the provider `Message` has no system role — the system prompt is a separate `system:` argument |
| `ToolSpec.json_schema` | `Tool.input_schema` (same idea, different name) |
| Explicit `TRANSITIONS` table | no turn-level state machine; `MissionStage` is a different, coarser lifecycle |
| Import boundaries enforced | not enforced or documented anywhere |

Migration is intentionally out of scope here. The point of writing the contract
first is that the next component built targets *this*, and the adapters that
bridge the old shapes are written knowingly rather than accreted.

---

## 7. Status

| Deliverable | State |
|---|---|
| `src/ronin/core/types.py` | ✅ types + transition table, **no logic** |
| `tests/core/test_types.py` | ✅ 83 tests, **100% statement + branch coverage** of the module |
| This document | ✅ |
| `src/ronin/core/loop.py` | ✅ see §9 |
| `src/ronin/providers/` | ✅ four adapters, normalizer, shim, router, cache-aware assembly, ledger — see [`docs/PROVIDERS.md`](PROVIDERS.md) |
| `src/ronin/tools/` | ✅ read/write/edit/multi_edit, glob/grep/ls, persistent bash, task, todo, net — see [`docs/TOOLS.md`](TOOLS.md) |
| `src/ronin/session.py` | ✅ the orchestrator seat; `task` wired to a nested turn on `router.for_subagent()` |
| Import-boundary enforcement test | ✅ `tests/tools/test_boundaries.py` — §3's table walked with `ast`, so a lazy import inside a function is caught too |
| `context/` `safety/` `agents/` `verify/` `persistence/` `ui/` `mcp/` | ✅ built, each mypy-strict, ruff clean, with unit + integration tests and an offline demo — see [`docs/SUBSYSTEMS.md`](SUBSYSTEMS.md) |
| `cli/` — the joins | see [`docs/SUBSYSTEMS.md`](SUBSYSTEMS.md) §2. This is the only layer whose mistakes no test below it can catch, which is why it is the thinnest package in the tree. |

**On the model layer and this contract.** The provider layer implements its own
`ModelClient` (`ModelRequest` in, `ModelDelta` out) rather than `core.protocols.ModelClient`
(`system`/`messages`/`tools` in, `ModelChunk` out), and `providers.bridge.LoopClient`
translates between them in about forty lines. Both seams are deliberate: the loop's
stays narrow so it need not change when a provider does, and the provider's needs a
request object so the cacheable prefix, the cache marker and the sampling knobs travel
together. **Whether to unify them is an open review ask** — the alternative is widening
`core.protocols` and the loop's 93 tests with fields the loop never reads. The bridge
is trivial on purpose so reversing this is cheap.

### `StreamReset`, and why it is in the union

A turn can be re-streamed from scratch — a provider retry after a mid-stream
drop, or a failover once tokens were already emitted. Without an explicit event
for it, a consumer renders the answer **twice**; the shipped provider layer
carries regression tests for exactly that duplication
(`test_stream_retry_dedup.py`, `test_stream_retry_render_dedup.py`).

Its scope is deliberately narrow: it invalidates the `TextDelta` events emitted
since the last `TurnStart` or `StreamReset`, whichever is later. **It says nothing
about tools** — a tool that already ran has already had its effect, and no event
can undo that.

**Review asks.** (1) Is `ApprovalDecision` the right way to keep approval out of
callbacks — see the open question in §8. (2) Should `DangerLevel` collapse the
five existing danger vocabularies (§6) in this pass, or is that migration work
for later?

---

## 9. The loop as built (`src/ronin/core/loop.py`)

```python
async def run_turn(state, model, tools, policy, *, system="",
                   max_iterations=100, max_tool_result_chars=16_000) -> AsyncIterator[Event]
```

Zero provider knowledge, zero UI knowledge: `model`, `tools`, and `policy` are
protocols (`src/ronin/core/protocols.py`), so a scripted fake replaces a provider
with no network and no monkeypatching.

**Stop conditions** — every one explicit, named on `TurnEnd.stop_reason`, and
separately tested:

| `StopReason` | Trigger | Ends in |
|---|---|---|
| `NO_TOOL_CALLS` | the model answered with text only | `DONE` |
| `MAX_ITERATIONS` | the iteration cap (default 100) | `ERROR` |
| `TOKEN_BUDGET` / `COST_BUDGET` | `policy.check_budget()` returned a reason | `DONE` |
| `INTERRUPTED` | `policy.cancelled()` | `INTERRUPTED` |
| `STALLED` | a repeat after a nudge | raises `StalledError` |

**Stall detection.** A call's fingerprint is `name` plus its arguments with sorted
keys, so `{"a":1,"b":2}` and `{"b":2,"a":1}` are the same action. Three
occurrences inside a rolling window of six inject a **system-role nudge**; a
further repeat raises `StalledError`. The error carries `agent_state`, because an
abort is still a checkpoint — and the outstanding calls are answered with a
synthetic result first, so the state it carries is genuinely sendable rather than
a transcript with dangling tool_use ids (a bug the demo caught).

**Truncation.** Every result the model sees goes through `truncate_for_model`,
which appends `…[truncated: N chars, M lines cut]`. The marker is the contract: the
model must be able to distinguish "the file ends here" from "we stopped showing
you the file".

**Parallelism.** A batch runs concurrently only when **every** approved call in it
is `READ_ONLY`; one mutating call makes the whole batch serial. Results are
re-ordered to the order the model asked in, so the transcript reads sequentially
either way.

**Interrupt.** `policy.cancelled()` is polled at every await point, and
`CancelledError` is caught around tool execution. A cancelled call gets
`ToolResult(ok=False, error="interrupted by user")`, so the transcript stays
well-formed and `TurnEnd.agent_state` is resumable.

**Demo:** `uv run python -m ronin.demo` — offline, no key, two scenarios showing
parallel execution, a denied approval, the truncation marker, the stall nudge, and
that state survives an abort.
