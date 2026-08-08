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
ToolStart(tool_use_id, name, arguments={})
ToolEnd(tool_use_id, name, result: ToolResult)
ApprovalRequest(tool_use_id, name, danger_level, rendered, reason="")
TurnEnd(turn_index, state, stop_reason="")
Error(message, kind="unknown", recoverable=False)

Event = TurnStart | TextDelta | ToolStart | ToolEnd | ApprovalRequest | TurnEnd | Error
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
                        │  core/types  │   imports nothing from ronin
                        └──────▲───────┘
        ┌──────────────┬───────┴───────┬──────────────┐
        │              │               │              │
   ┌────┴─────┐  ┌─────┴────┐   ┌──────┴─────┐  ┌─────┴──────┐
   │providers/│  │  tools/   │   │  context/  │  │  safety/   │
   └────▲─────┘  └─────▲────┘   └──────▲─────┘  └─────▲──────┘
        │              │               │              │
        └──────────────┴───────┬───────┴──────────────┘
                               │
                        ┌──────┴───────┐
                        │    loop/     │   imports all of the above
                        └──────▲───────┘
                        ┌──────┴───────┐
                        │ orchestrator/│   session, interrupt, resume
                        └──────▲───────┘
        ┌──────────────┬───────┴───────┬──────────────┐
   ┌────┴─────┐  ┌─────┴────┐   ┌──────┴─────┐  ┌─────┴──────┐
   │   tui/   │  │ headless/│   │    sdk/    │  │   lsp/     │
   └──────────┘  └──────────┘   └────────────┘  └────────────┘
```

**The rules, stated as prohibitions:**

| Rule | Rationale |
|---|---|
| `core/` imports **nothing** from `ronin.*` | It is the shared vocabulary; a dependency here would make every other rule unenforceable. |
| **nothing in `tools/` may import from `providers/`** | A tool that knows which model is calling it will special-case for one. Tools take arguments and return a `ToolResult`, full stop. |
| **nothing in `providers/` may import from `tools/`** | An adapter that imports tools ends up executing them, which is how "the provider layer" quietly becomes a second agent loop. Adapters receive `ToolSpec` *data*. |
| `loop/` imports both | It is the only place allowed to know about both halves — that is its job. |
| `tui/`, `headless/`, `sdk/`, `lsp/` may import `core/` and `orchestrator/`, never `providers/` or `tools/` | A UI that reaches into the tool layer will eventually execute something. |
| **nothing in `loop/`, `providers/`, `tools/` may import `tui/`** | The inverse of §4. |
| `safety/` and `context/` depend only on `core/` | They must be testable against types, not against a live model or a live filesystem. |

`ToolSpec` carrying no handler (§1.2) is what makes the `providers/`↮`tools/`
prohibition *natural* rather than aspirational: the provider layer needs tool
**descriptions**, and descriptions are core types.

The rule is currently documentation. It should be enforced by a test that walks
the import graph and fails on a violation — cheap, and the only version of this
rule that survives contact with a deadline. **Not yet written** (this deliverable
is types + contract only); it is the first thing to add alongside `loop/`.

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

**Why `Generator`, not `Iterator`.** Approval is inherently request/response, and
a pure iterator cannot carry a reply — which is exactly where a callback would
sneak back in and re-invert control. `ApprovalDecision` is the *only* value a
consumer ever sends, so the channel stays narrow: one event type expects a reply,
everything else is fire-and-forget (`send(None)`).

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
| `tests/core/test_types.py` | ✅ 80 tests, **100% statement + branch coverage** of the module |
| This document | ✅ |
| Import-boundary enforcement test | ❌ not yet — first addition alongside `loop/` |
| Everything in the §0 diagram | ❌ not built; this is the contract it will be built against |

### One known gap in the event union, flagged rather than silently added

The `Event` union above is exactly the seven types specified. The shipped
provider layer emits a **third** stream event the union does not cover:
`StreamEvent(type="reset")`, meaning *"a retry re-streamed this turn from
scratch — discard what you already rendered"*. It exists because failing over or
retrying after partial output otherwise duplicates the answer on screen, and the
repo carries regression tests for exactly that (`test_stream_retry_dedup.py`,
`test_stream_retry_render_dedup.py`).

Without an equivalent, any consumer built on this contract will re-render
duplicated text on a mid-stream retry. The fix is one more member —
`StreamReset(reason: str = "")` — but it widens a union the work order specified
explicitly, so it is a **review ask, not a unilateral addition**.

**Review asks.** (1) Add `StreamReset` to the union? (2) Is `ApprovalDecision`
(and therefore `Generator` rather than `Iterator`) the right way to keep approval
out of callbacks? (3) Should `DangerLevel` collapse the five existing danger
vocabularies (§6) in this pass, or is that migration work for later?
