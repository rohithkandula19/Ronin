"""The core contract: the data types every layer of Ronin agrees on.

This module is deliberately **logic-free**. It holds frozen dataclasses, enums,
and one transition table — no I/O, no provider calls, no tool execution, no UI.
Everything here is importable by every other package, and this module imports
nothing from Ronin (see ``docs/ARCHITECTURE.md`` §3 for the dependency rule).

Three invariants are worth stating up front, because the validators below exist
to enforce them rather than to be polite:

1. **Pairing.** Every ``ToolUse`` must be answered by exactly one ``ToolResult``
   carrying the same ``tool_use_id``. Providers reject an unpaired transcript,
   so a type that can represent one is a type that can crash a session.
2. **Tool-call ids are opaque and unique.** Duplicate ids across turns have
   caused real provider 400s, so an id is required and non-empty.
3. **Approval is not inferable.** A tool's danger level and its approval
   requirement are separate fields, and the dangerous ones cannot silently
   default to "no approval needed".
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

# --------------------------------------------------------------------------- #
# Content blocks
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Text:
    """Plain prose from the user or the model."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("Text.text must be a str")


@dataclass(frozen=True, slots=True)
class Thinking:
    """Reasoning content the provider exposes separately from the answer.

    ``signature`` carries provider-opaque bytes (e.g. a thought signature) that
    must be replayed verbatim on the follow-up turn or the provider rejects the
    continuation. Never synthesize or edit it.
    """

    text: str
    signature: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("Thinking.text must be a str")


@dataclass(frozen=True, slots=True)
class ToolUse:
    """The model's request to call one tool."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ToolUse.id is required and must be non-empty")
        if not self.name:
            raise ValueError("ToolUse.name is required and must be non-empty")
        if not isinstance(self.arguments, Mapping):
            raise TypeError(
                "ToolUse.arguments must be a mapping — a provider that hands "
                "back a JSON *string* is normalized in the provider layer, not here"
            )


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    """The transcript record of one tool's outcome, paired to a ``ToolUse``.

    Distinct from :class:`ToolResult`, which is what a tool *returns*. This is
    the conversation-shaped view: the id it answers, and the text the model sees.
    """

    tool_use_id: str
    content: str
    is_error: bool = False

    def __post_init__(self) -> None:
        if not self.tool_use_id:
            raise ValueError("ToolResultBlock.tool_use_id is required")


#: The closed set of content-block types. A block is one of exactly these.
ContentBlock = Text | ToolUse | ToolResultBlock | Thinking

_BLOCK_TYPES: tuple[type, ...] = (Text, ToolUse, ToolResultBlock, Thinking)


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #


class Role(str, Enum):
    """Who produced a message."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class Message:
    """One turn in the transcript, as a sequence of typed content blocks.

    Content is *always* a sequence of blocks, never a bare string — a string
    cannot express a tool call, and mixing the two representations is how
    pairing bugs get in.
    """

    role: Role
    content_blocks: tuple[ContentBlock, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            raise TypeError("Message.role must be a Role")
        if isinstance(self.content_blocks, (str, bytes)):
            raise TypeError(
                "Message.content_blocks must be a sequence of blocks, not a string"
            )
        if not isinstance(self.content_blocks, tuple):
            raise TypeError("Message.content_blocks must be a tuple (frozen)")
        for block in self.content_blocks:
            if not isinstance(block, _BLOCK_TYPES):
                raise TypeError(f"unknown content block type: {type(block).__name__}")
        if self.role is not Role.ASSISTANT:
            for block in self.content_blocks:
                if isinstance(block, (ToolUse, Thinking)):
                    raise ValueError(
                        f"{type(block).__name__} may only appear on an assistant "
                        f"message, not {self.role.value}"
                    )

    @property
    def tool_uses(self) -> tuple[ToolUse, ...]:
        return tuple(b for b in self.content_blocks if isinstance(b, ToolUse))

    @property
    def tool_results(self) -> tuple[ToolResultBlock, ...]:
        return tuple(b for b in self.content_blocks if isinstance(b, ToolResultBlock))

    @property
    def text(self) -> str:
        """Concatenated prose. Excludes thinking — reasoning is not the answer."""
        return "".join(b.text for b in self.content_blocks if isinstance(b, Text))


def unpaired_tool_uses(messages: Sequence[Message]) -> tuple[str, ...]:
    """Tool-use ids with no answering result, in order of appearance.

    The pairing invariant, as a pure function: a transcript with unpaired ids is
    one a provider will reject, so compaction, truncation, and interrupt handling
    must all be checked against this.
    """
    answered = {
        block.tool_use_id
        for message in messages
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock)
    }
    return tuple(
        block.id
        for message in messages
        for block in message.content_blocks
        if isinstance(block, ToolUse) and block.id not in answered
    )


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


class DangerLevel(int, Enum):
    """How much damage a tool can do if the model is wrong.

    Ordered so comparisons are meaningful (``level >= DangerLevel.MUTATING``).
    """

    READ_ONLY = 0
    MUTATING = 1
    DESTRUCTIVE = 2
    IRREVERSIBLE = 3


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """The declaration of a tool: what it is called, and how dangerous it is.

    Carries no handler. A spec is data — describable, serializable, and safe to
    send to a model — while the callable lives in the tool layer's registry.
    """

    name: str
    description: str
    json_schema: Mapping[str, Any] = field(default_factory=dict)
    danger_level: DangerLevel = DangerLevel.READ_ONLY
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolSpec.name is required")
        if not self.description:
            raise ValueError(
                "ToolSpec.description is required — it is the only thing the "
                "model has to decide whether to call this tool"
            )
        if not isinstance(self.danger_level, DangerLevel):
            raise TypeError("ToolSpec.danger_level must be a DangerLevel")
        if self.danger_level >= DangerLevel.DESTRUCTIVE and not self.requires_approval:
            raise ValueError(
                f"tool {self.name!r} is {self.danger_level.name} but declares "
                "requires_approval=False — a destructive tool cannot opt out of "
                "the gate at declaration time"
            )


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool returns. Success and failure are the same shape.

    ``ok=False`` with a populated ``error`` is a *normal* outcome fed back to the
    model as an observation; tools do not raise to signal task failure.
    """

    ok: bool
    content: str = ""
    error: str = ""
    artifacts: tuple[str, ...] = ()
    tokens_estimate: int = 0

    def __post_init__(self) -> None:
        if self.ok and self.error:
            raise ValueError("ToolResult cannot be ok=True and carry an error")
        if not self.ok and not self.error:
            raise ValueError(
                "a failed ToolResult must say why — an empty error gives the "
                "model nothing to recover from"
            )
        if self.tokens_estimate < 0:
            raise ValueError("ToolResult.tokens_estimate must be >= 0")
        if not isinstance(self.artifacts, tuple):
            raise TypeError("ToolResult.artifacts must be a tuple (frozen)")

    def as_block(self, tool_use_id: str) -> ToolResultBlock:
        """Project into the transcript, preserving the ok/error distinction."""
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=self.content if self.ok else self.error,
            is_error=not self.ok,
        )


# --------------------------------------------------------------------------- #
# Agent state
# --------------------------------------------------------------------------- #


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class Todo:
    """One unit of the agent's own plan."""

    id: str
    subject: str
    status: TodoStatus = TodoStatus.PENDING

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Todo.id is required")
        if not self.subject:
            raise ValueError("Todo.subject is required")
        if not isinstance(self.status, TodoStatus):
            raise TypeError("Todo.status must be a TodoStatus")


@dataclass(frozen=True, slots=True)
class Budget:
    """Hard ceilings for one session. ``None`` means unbounded.

    Spend is tracked separately from the limits so a budget can be checked
    without mutating it, and so a resumed run inherits both.
    """

    max_tokens: int | None = None
    max_usd: float | None = None
    max_wall_seconds: float | None = None
    spent_tokens: int = 0
    spent_usd: float = 0.0
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        for name in ("max_tokens", "max_usd", "max_wall_seconds"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"Budget.{name} must be positive or None")
        for name in ("spent_tokens", "spent_usd", "elapsed_seconds"):
            if getattr(self, name) < 0:
                raise ValueError(f"Budget.{name} must be >= 0")

    @property
    def exhausted(self) -> bool:
        return bool(
            (self.max_tokens is not None and self.spent_tokens >= self.max_tokens)
            or (self.max_usd is not None and self.spent_usd >= self.max_usd)
            or (
                self.max_wall_seconds is not None
                and self.elapsed_seconds >= self.max_wall_seconds
            )
        )


class Mode(str, Enum):
    """How much the agent may do without asking.

    Deliberately a permissiveness ladder rather than a set of booleans, so
    "more permissive than X" is expressible. ``PLAN`` mutates nothing.
    """

    PLAN = "plan"
    ASK = "ask"
    AUTO_EDIT = "auto_edit"
    FULL = "full"

    @property
    def rank(self) -> int:
        return _MODE_RANK[self]

    def at_least(self, other: Mode) -> bool:
        return self.rank >= other.rank


_MODE_RANK: dict[Mode, int] = {
    Mode.PLAN: 0,
    Mode.ASK: 1,
    Mode.AUTO_EDIT: 2,
    Mode.FULL: 3,
}


@dataclass(frozen=True, slots=True)
class AgentState:
    """Everything needed to resume a session, and nothing else.

    Frozen on purpose: the loop advances state by producing a new value
    (``replace``), so a checkpoint is a value that cannot be mutated behind the
    UI's back, and "resume from checkpoint" is just "use that value".
    """

    messages: tuple[Message, ...] = ()
    todos: tuple[Todo, ...] = ()
    cwd: str = "."
    budget: Budget = field(default_factory=Budget)
    mode: Mode = Mode.ASK
    checkpoint_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple):
            raise TypeError("AgentState.messages must be a tuple (frozen)")
        if not isinstance(self.todos, tuple):
            raise TypeError("AgentState.todos must be a tuple (frozen)")
        if not isinstance(self.mode, Mode):
            raise TypeError("AgentState.mode must be a Mode")
        if not self.cwd:
            raise ValueError("AgentState.cwd must be non-empty ('.' for relative)")
        if len({todo.id for todo in self.todos}) != len(self.todos):
            raise ValueError("AgentState.todos contains duplicate ids")
        if self.checkpoint_id is not None and not self.checkpoint_id:
            raise ValueError("AgentState.checkpoint_id must be None or non-empty")

    @property
    def pairing_errors(self) -> tuple[str, ...]:
        """Unpaired tool-use ids. Empty tuple means the transcript is sendable."""
        return unpaired_tool_uses(self.messages)

    def with_message(self, message: Message) -> AgentState:
        return replace(self, messages=self.messages + (message,))


# --------------------------------------------------------------------------- #
# Turn state machine
# --------------------------------------------------------------------------- #


class TurnState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    TOOL_PENDING = "tool_pending"
    AWAITING_APPROVAL = "awaiting_approval"
    TOOL_RUNNING = "tool_running"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    DONE = "done"
    ERROR = "error"
    INTERRUPTED = "interrupted"


#: The only legal transitions. Anything absent here is a bug, not a nuance.
#:
#: ERROR and INTERRUPTED are terminal-ish: they end a turn but can resume into
#: THINKING (retry / continue) or settle into DONE, which is why they are not
#: leaves. DONE and IDLE are the true resting states.
TRANSITIONS: Mapping[TurnState, frozenset[TurnState]] = {
    TurnState.IDLE: frozenset({TurnState.THINKING}),
    TurnState.THINKING: frozenset(
        {
            TurnState.TOOL_PENDING,
            TurnState.VERIFYING,
            TurnState.DONE,
            TurnState.ERROR,
            TurnState.INTERRUPTED,
        }
    ),
    TurnState.TOOL_PENDING: frozenset(
        {
            TurnState.AWAITING_APPROVAL,
            TurnState.TOOL_RUNNING,
            TurnState.ERROR,
            TurnState.INTERRUPTED,
        }
    ),
    TurnState.AWAITING_APPROVAL: frozenset(
        {
            TurnState.TOOL_RUNNING,
            TurnState.OBSERVING,
            TurnState.ERROR,
            TurnState.INTERRUPTED,
        }
    ),
    TurnState.TOOL_RUNNING: frozenset(
        {TurnState.OBSERVING, TurnState.ERROR, TurnState.INTERRUPTED}
    ),
    TurnState.OBSERVING: frozenset(
        {
            TurnState.THINKING,
            TurnState.VERIFYING,
            TurnState.DONE,
            TurnState.ERROR,
            TurnState.INTERRUPTED,
        }
    ),
    TurnState.VERIFYING: frozenset(
        {
            TurnState.THINKING,
            TurnState.DONE,
            TurnState.ERROR,
            TurnState.INTERRUPTED,
        }
    ),
    TurnState.DONE: frozenset({TurnState.IDLE}),
    TurnState.ERROR: frozenset({TurnState.THINKING, TurnState.DONE, TurnState.IDLE}),
    TurnState.INTERRUPTED: frozenset(
        {TurnState.THINKING, TurnState.DONE, TurnState.IDLE}
    ),
}

#: States a turn may sit in indefinitely without the loop being wedged.
RESTING_STATES: frozenset[TurnState] = frozenset(
    {TurnState.IDLE, TurnState.DONE, TurnState.AWAITING_APPROVAL}
)


def can_transition(source: TurnState, target: TurnState) -> bool:
    """Pure predicate over :data:`TRANSITIONS`."""
    return target in TRANSITIONS[source]


class IllegalTransition(RuntimeError):
    """Raised when the loop attempts a transition the table forbids."""

    def __init__(self, source: TurnState, target: TurnState) -> None:
        super().__init__(
            f"illegal turn transition {source.value} -> {target.value}; "
            f"legal targets: {sorted(s.value for s in TRANSITIONS[source])}"
        )
        self.source = source
        self.target = target


def assert_transition(source: TurnState, target: TurnState) -> TurnState:
    """Return ``target`` if the transition is legal, else raise."""
    if not can_transition(source, target):
        raise IllegalTransition(source, target)
    return target


# --------------------------------------------------------------------------- #
# Events — the loop's only output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TurnStart:
    turn_index: int
    state: TurnState = TurnState.THINKING

    def __post_init__(self) -> None:
        if self.turn_index < 0:
            raise ValueError("TurnStart.turn_index must be >= 0")


@dataclass(frozen=True, slots=True)
class TextDelta:
    """An incremental chunk of assistant prose.

    ``thinking=True`` marks reasoning, which a consumer may render differently
    or hide entirely — the distinction lives in the event, not in the renderer's
    guesswork.
    """

    text: str
    thinking: bool = False


@dataclass(frozen=True, slots=True)
class StreamReset:
    """Discard the text already rendered for this turn and start over.

    Emitted when a turn is re-streamed from scratch — a provider retry after a
    mid-stream drop, or a failover to another provider once tokens were already
    sent. Without it a consumer renders the answer twice; the shipped provider
    layer carries regression tests for exactly that duplication.

    Scope is deliberately narrow: it invalidates the :class:`TextDelta` events
    emitted since the last ``TurnStart`` or ``StreamReset``, whichever is later.
    It says nothing about tools — a tool that already ran has already had its
    effect, and no event can undo that.
    """

    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str):
            raise TypeError("StreamReset.reason must be a str")


@dataclass(frozen=True, slots=True)
class ToolStart:
    tool_use_id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_use_id:
            raise ValueError("ToolStart.tool_use_id is required")


@dataclass(frozen=True, slots=True)
class ToolEnd:
    tool_use_id: str
    name: str
    result: ToolResult

    def __post_init__(self) -> None:
        if not self.tool_use_id:
            raise ValueError("ToolEnd.tool_use_id is required")
        if not isinstance(self.result, ToolResult):
            raise TypeError("ToolEnd.result must be a ToolResult")


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """The loop asking a human to decide, and blocking until it hears back.

    The event carries the *rendered* command/diff the human is deciding on, so
    what is shown is what will run — a consumer must never re-derive it.
    """

    tool_use_id: str
    name: str
    danger_level: DangerLevel
    rendered: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.tool_use_id:
            raise ValueError("ApprovalRequest.tool_use_id is required")
        if not self.rendered:
            raise ValueError(
                "ApprovalRequest.rendered is required — a human cannot approve "
                "what is not shown to them"
            )
        if not isinstance(self.danger_level, DangerLevel):
            raise TypeError("ApprovalRequest.danger_level must be a DangerLevel")


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """A human's answer to an :class:`ApprovalRequest`.

    This is the *only* value a consumer sends back into the loop, and it is why
    the loop is a ``Generator`` rather than a bare ``Iterator``: approval is
    inherently request/response, and a pure iterator cannot express it without
    reintroducing callbacks (see ``docs/ARCHITECTURE.md`` §4).

    ``remember`` asks for the decision to be persisted as a standing rule. It is
    a *request*, not an authorization — policy decides whether a rule may be
    stored, so a tool that must always be gated cannot be waived by remembering.
    """

    approved: bool
    reason: str = ""
    remember: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str):
            raise TypeError("ApprovalDecision.reason must be a str")


#: What a consumer with no human attached must return. Never auto-allow.
DENY_UNATTENDED = ApprovalDecision(
    approved=False, reason="no human attached to approve this action"
)


@dataclass(frozen=True, slots=True)
class TurnEnd:
    turn_index: int
    state: TurnState
    stop_reason: str = ""

    def __post_init__(self) -> None:
        if self.turn_index < 0:
            raise ValueError("TurnEnd.turn_index must be >= 0")
        if self.state not in _TURN_END_STATES:
            raise ValueError(
                f"TurnEnd.state must be one of "
                f"{sorted(s.value for s in _TURN_END_STATES)}, got {self.state.value}"
            )


_TURN_END_STATES: frozenset[TurnState] = frozenset(
    {TurnState.DONE, TurnState.ERROR, TurnState.INTERRUPTED}
)


@dataclass(frozen=True, slots=True)
class Error:
    """A failure surfaced to consumers. ``recoverable`` decides resumability."""

    message: str
    kind: str = "unknown"
    recoverable: bool = False

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("Error.message is required")


#: The closed set of events the loop emits. Consumers switch on exactly these.
Event = (
    TurnStart
    | TextDelta
    | StreamReset
    | ToolStart
    | ToolEnd
    | ApprovalRequest
    | TurnEnd
    | Error
)

EVENT_TYPES: tuple[type, ...] = (
    TurnStart,
    TextDelta,
    StreamReset,
    ToolStart,
    ToolEnd,
    ApprovalRequest,
    TurnEnd,
    Error,
)


def is_event(value: object) -> bool:
    """Whether ``value`` is a member of the closed :data:`Event` union."""
    return isinstance(value, EVENT_TYPES)


__all__ = [
    "AgentState",
    "ApprovalDecision",
    "ApprovalRequest",
    "Budget",
    "ContentBlock",
    "DENY_UNATTENDED",
    "DangerLevel",
    "EVENT_TYPES",
    "Error",
    "Event",
    "IllegalTransition",
    "Message",
    "Mode",
    "RESTING_STATES",
    "Role",
    "StreamReset",
    "TRANSITIONS",
    "Text",
    "TextDelta",
    "Thinking",
    "Todo",
    "TodoStatus",
    "ToolEnd",
    "ToolResult",
    "ToolResultBlock",
    "ToolSpec",
    "ToolStart",
    "ToolUse",
    "TurnEnd",
    "TurnStart",
    "TurnState",
    "assert_transition",
    "can_transition",
    "is_event",
    "unpaired_tool_uses",
]
