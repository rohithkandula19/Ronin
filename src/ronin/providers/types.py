"""The provider layer's vocabulary: what goes out, what comes back, what a model can do.

Everything here is a frozen stdlib dataclass. The point of the layer is that
swapping Claude for Kimi K2 or a local Qwen is a *config* change, so nothing in
this module may mention a specific vendor — vendor knowledge lives in the
adapters, and the only thing that leaks upward is :class:`Capabilities`.

``Capabilities`` is deliberately a set of facts rather than a model name. Code
that branches on ``if model == "claude-…"`` breaks the moment someone points
``base_url`` at a proxy; code that branches on ``caps.native_tools`` keeps
working, because that is the property it actually cares about.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from ..core.types import Message, ToolSpec, ToolUse

# --------------------------------------------------------------------------- #
# What a model can do
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Capabilities:
    """The facts about a model that change how we talk to it.

    ``native_tools=False`` is the important one: it routes the request through
    the format shim (see :mod:`ronin.providers.shim`), which renders tool specs
    into the system prompt and parses calls back out of the text stream. Every
    other field only ever *removes* work — no field here may be required for the
    layer to function, or a new provider becomes a code change again.
    """

    native_tools: bool
    parallel_tools: bool
    prompt_cache: bool
    thinking: bool
    max_context: int
    vision: bool

    def __post_init__(self) -> None:
        if self.max_context <= 0:
            raise ValueError("Capabilities.max_context must be positive")
        if self.parallel_tools and not self.native_tools:
            # The shim emits one call per tagged block and we execute them in
            # order; claiming parallel dispatch on top of that would tell the
            # loop it may reorder calls it must not reorder.
            raise ValueError(
                "parallel_tools requires native_tools — the format shim is "
                "strictly sequential, so a shimmed model cannot promise parallel calls"
            )


#: What we assume when a provider tells us nothing. Deliberately the *narrow*
#: end of every axis: a wrong "can't do that" costs a slower path, a wrong "can"
#: costs a failed request the user has to debug.
CONSERVATIVE = Capabilities(
    native_tools=False,
    parallel_tools=False,
    prompt_cache=False,
    thinking=False,
    max_context=8192,
    vision=False,
)


# --------------------------------------------------------------------------- #
# What goes out
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One call to one model.

    ``system``, ``tools`` and ``prefix`` are separated from ``messages`` because
    they are the *stable* part of the prompt: :mod:`ronin.providers.assembly`
    depends on being able to render them byte-identically across turns, which is
    what makes prompt caching work at all.
    """

    model: str
    system: str = ""
    messages: tuple[Message, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    #: Stable context that sits with the system prompt (e.g. a repo map). Kept
    #: separate so assembly can order it deterministically.
    prefix: str = ""
    max_tokens: int = 4096
    temperature: float | None = None
    #: Tokens the model may spend on reasoning. Ignored unless
    #: ``capabilities().thinking``.
    thinking_budget: int = 0
    #: Index into the rendered stable blocks that should carry a cache marker.
    #: ``-1`` means "no marker"; assembly sets it, adapters honour it.
    cache_marker: int = -1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model:
            raise ValueError("ModelRequest.model is required")
        if not isinstance(self.messages, tuple):
            raise TypeError("ModelRequest.messages must be a tuple (frozen)")
        if not isinstance(self.tools, tuple):
            raise TypeError("ModelRequest.tools must be a tuple (frozen)")
        if self.max_tokens <= 0:
            raise ValueError("ModelRequest.max_tokens must be positive")
        if self.thinking_budget < 0:
            raise ValueError("ModelRequest.thinking_budget must be >= 0")

    def with_messages(self, messages: Sequence[Message]) -> ModelRequest:
        """The same request against a different transcript.

        Used by the shim's repair loop: everything stable stays byte-identical,
        so the repair round-trip is still a cache hit.
        """
        return replace(self, messages=tuple(messages))

    def with_system(self, system: str) -> ModelRequest:
        """The same request with a rewritten system prompt (the shim injects specs)."""
        return replace(self, system=system)


# --------------------------------------------------------------------------- #
# What comes back
# --------------------------------------------------------------------------- #


class FinishReason(StrEnum):
    """Why the model stopped, normalized across providers.

    ``UNKNOWN`` is kept rather than folded into ``STOP``: a provider we have not
    taught this layer about should look unfamiliar in the logs, not look normal.
    """

    STOP = "stop"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Usage:
    """Token and cost accounting for one request.

    Cache fields are separate from ``input_tokens`` because providers disagree
    about whether cached reads are billed as input: keeping them apart lets
    :mod:`ronin.providers.accounting` price each provider correctly and lets us
    measure a real cache hit rate instead of guessing one.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"Usage.{name} must be >= 0")
        if self.cost_usd < 0:
            raise ValueError("Usage.cost_usd must be >= 0")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cacheable_input(self) -> int:
        """Input tokens that were eligible for the cache, hit or miss."""
        return self.cache_read_tokens + self.cache_write_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


# --------------------------------------------------------------------------- #
# The stream
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A chunk of the visible answer."""

    text: str


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    """A chunk of reasoning, which is *not* the answer.

    Separate from :class:`TextDelta` so a consumer can render it differently or
    drop it, and so the shim never scans reasoning for tool-call tags.
    """

    text: str
    signature: str = ""


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """A fragment of one tool call, exactly as the provider sent it.

    This is the *un-normalized* shape and it is intentionally forgiving: ``id``
    and ``name`` arrive on some chunk (not necessarily the first), and
    ``arguments_fragment`` is a piece of a JSON string that is only valid once
    concatenated. :mod:`ronin.providers.normalize` turns a stream of these into
    ``ToolUse``. Consumers that want tool calls should wait for
    :class:`Completed` rather than trying to interpret these.
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments_fragment: str = ""

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("ToolCallDelta.index must be >= 0")


@dataclass(frozen=True, slots=True)
class StreamReset:
    """A retry re-streamed this turn; discard everything emitted so far.

    Mirrors ``ronin.core.types.StreamReset`` — without it, a mid-stream retry
    prints the answer twice.
    """

    reason: str = ""


@dataclass(frozen=True, slots=True)
class Completed:
    """The terminal delta: the assembled message plus accounting.

    ``message`` already contains the normalized ``ToolUse`` blocks, so a
    consumer never has to touch :class:`ToolCallDelta`. ``notes`` records what
    normalization had to repair — an empty tuple means the provider was
    well-behaved, and a non-empty one is the evidence for a bug report.
    """

    message: Message
    finish: FinishReason = FinishReason.STOP
    usage: Usage = field(default_factory=Usage)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.finish, FinishReason):
            raise TypeError("Completed.finish must be a FinishReason")
        if not isinstance(self.notes, tuple):
            raise TypeError("Completed.notes must be a tuple (frozen)")

    @property
    def tool_uses(self) -> tuple[ToolUse, ...]:
        return self.message.tool_uses


#: One item from a provider stream. Exactly one :class:`Completed` ends a stream.
ModelDelta = TextDelta | ThinkingDelta | ToolCallDelta | StreamReset | Completed

DELTA_TYPES: tuple[type, ...] = (
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    StreamReset,
    Completed,
)


def is_delta(value: object) -> bool:
    """Whether ``value`` is a member of the :data:`ModelDelta` union."""
    return isinstance(value, DELTA_TYPES)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ProviderError(RuntimeError):
    """A request could not be completed at all.

    Distinct from ``Completed(finish=ERROR)``: that is a turn the model finished
    badly, this is a turn that never happened (transport refused, credentials
    missing, endpoint unreachable). The distinction matters because the first is
    something to show the model and the second is something to show the user.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        retryable: bool = False,
        retry_after: str = "",
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        #: The server's ``Retry-After`` header, verbatim and unparsed. A server
        #: knows its own rate-limit window better than our backoff ladder does, so
        #: the retry policy prefers this when it is a number it can read.
        self.retry_after = retry_after
