"""Tool calling for models that have none, without leaking the mechanism to the user.

When ``capabilities().native_tools`` is False we stop asking the API for tool
calls and start asking the *model* for them: specs go into the system prompt as a
strict XML block, and calls come back as ``<ronin:tool_call>{…}</ronin:tool_call>``.

Two things make this hard, and both are the reason this is a state machine rather
than a regex:

1. **Partial tags must not leak.** The tag arrives split across chunks —
   ``"<ronin:to"`` then ``"ol_call>"``. A regex over the finished string works
   only if you have the finished string, by which time you have already printed
   the fragment to the user's terminal. :class:`ShimStreamParser` withholds any
   suffix that could still become a tag and releases it the moment it cannot.
2. **A bad call must not crash the turn.** A model that writes
   ``<ronin:tool_call>{'path': 'x',}</ronin:tool_call>`` gets the payload back
   with the exact format restated, up to :data:`MAX_REPAIRS` times. After that it
   surfaces to the user rather than looping forever — an agent silently retrying
   a malformed call is indistinguishable from a hang.

The tag is namespaced (``ronin:``) on purpose. Bare ``<tool_call>`` collides with
the chat template of several fine-tunes, which emit it themselves and would make
every response look like a call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace

from ..core.types import ContentBlock, Message, Role, Text, ToolSpec
from .base import ModelClient
from .jsonargs import find_json_objects, parse_arguments
from .normalize import NormalizedCalls, ToolCallAccumulator
from .types import (
    Capabilities,
    Completed,
    FinishReason,
    ModelDelta,
    ModelRequest,
    StreamReset,
    TextDelta,
    ThinkingDelta,
    Usage,
)

OPEN_TAG = "<ronin:tool_call>"
CLOSE_TAG = "</ronin:tool_call>"

#: How many times we re-ask a model to fix its own tool call before giving up.
#: Two is not arbitrary: one retry catches a formatting slip, a second catches a
#: model that needed the format restated. A third has never helped in practice —
#: by then the model does not understand the format and the user needs to know.
MAX_REPAIRS = 2


# --------------------------------------------------------------------------- #
# Rendering specs into the prompt
# --------------------------------------------------------------------------- #


def render_tool_specs(specs: Sequence[ToolSpec]) -> str:
    """The tool block that goes into the system prompt.

    Strict, closed XML with the JSON schema inline. The format instructions are
    stated once here and re-used verbatim by the repair message, so a model can
    never be corrected toward a format different from the one it was given.
    """
    if not specs:
        return ""
    lines = ["<ronin:tools>"]
    for spec in specs:
        schema = json.dumps(spec.json_schema, sort_keys=True) if spec.json_schema else "{}"
        lines.append("  <ronin:tool>")
        lines.append(f"    <name>{spec.name}</name>")
        lines.append(f"    <description>{spec.description}</description>")
        lines.append(f"    <parameters>{schema}</parameters>")
        lines.append("  </ronin:tool>")
    lines.append("</ronin:tools>")
    lines.append("")
    lines.append(FORMAT_RULES)
    return "\n".join(lines)


FORMAT_RULES = """\
<ronin:tool_call_format>
To call a tool, emit exactly this, and nothing else on those lines:
<ronin:tool_call>{"name": "TOOL_NAME", "arguments": {"ARG": "VALUE"}}</ronin:tool_call>

Rules:
- One <ronin:tool_call> block per call. Emit several blocks to make several calls.
- The block body is a single JSON object with exactly two keys: "name" and "arguments".
- "arguments" is a JSON object, never a string. No markdown fences, no trailing commas.
- Say what you are about to do in prose *before* the block, not inside it.
- Do not describe a call you are not making; a block is an action, not an example.
</ronin:tool_call_format>"""


def build_shim_system(system: str, specs: Sequence[ToolSpec]) -> str:
    """Splice the tool block onto a system prompt, tools last.

    Tools go *after* the base prompt because they are the part most likely to be
    identical across turns, and a stable tail is what a prefix cache rewards.
    """
    block = render_tool_specs(specs)
    if not block:
        return system
    return f"{system}\n\n{block}" if system else block


def repair_message(raw_payload: str, error: str) -> Message:
    """The user-role turn that tells the model its call did not parse.

    Quotes the payload back verbatim. A paraphrase invites the model to fix the
    paraphrase, and restates the format from the same constant the system prompt
    used, so the two can never drift apart.
    """
    body = (
        "Your tool call did not parse and was not executed.\n\n"
        f"Reason: {error}\n\n"
        "You wrote:\n"
        f"{raw_payload}\n\n"
        "Here is the exact format. Emit the corrected call and nothing else.\n\n"
        f"{FORMAT_RULES}"
    )
    return Message(role=Role.USER, content_blocks=(Text(body),))


# --------------------------------------------------------------------------- #
# The streaming parser
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ShimCall:
    """One call extracted from the text stream, still un-normalized."""

    name: str
    raw_arguments: str


@dataclass(frozen=True, slots=True)
class ShimFailure:
    """A block that looked like a call but could not be read as one."""

    raw: str
    error: str


@dataclass(frozen=True, slots=True)
class ShimEmit:
    """What one ``feed`` produced: text safe to show, plus finished blocks."""

    visible: str = ""
    calls: tuple[ShimCall, ...] = ()
    failures: tuple[ShimFailure, ...] = ()


def _held_suffix_len(text: str, token: str) -> int:
    """Length of the longest suffix of ``text`` that is a proper prefix of ``token``.

    This is the whole trick. ``"...and then <ronin:to"`` must hold back 11
    characters; ``"a < b"`` must hold back one (``<`` could still begin the tag)
    and release it as soon as ``b`` proves it cannot.
    """
    limit = min(len(text), len(token) - 1)
    for size in range(limit, 0, -1):
        if text.endswith(token[:size]):
            return size
    return 0


def _find_close_outside_string(text: str) -> int:
    """Index of the first :data:`CLOSE_TAG` that begins *outside* a JSON string.

    A plain ``find`` is wrong here, and wrong in the worst way. Ask a model to
    write a file documenting this very protocol and it emits::

        <ronin:tool_call>{"name":"write_file","arguments":{"content":"</ronin:tool_call>"}}</ronin:tool_call>

    A naive scan cuts at the *first* close tag — inside the string — so the payload
    becomes ``{"name":…,"content":"`` which :func:`close_truncated` cheerfully
    repairs into ``{"content": ""}``, and the remaining ``"}}</ronin:tool_call>``
    is emitted as prose. Two silent failures at once: the file is written empty,
    and the tag leaks into the visible answer.

    Every scanner in :mod:`ronin.providers.jsonargs` is string-aware for exactly
    this reason; this is the same discipline applied to the tag search.
    """
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char == "<" and text.startswith(CLOSE_TAG, index):
            return index
        index += 1
    return -1


class ShimStreamParser:
    """Splits a text stream into visible prose and tool-call blocks.

    Feed it chunks in order; call :meth:`finish` at end of stream. The parser
    never emits a byte that might still turn out to be part of a tag, and never
    emits a call's payload as prose — an unterminated block becomes a failure, not
    visible text, because leaking half a JSON object into the answer is worse than
    reporting that the model got cut off.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_call = False

    def feed(self, chunk: str) -> ShimEmit:
        """Consume one chunk of model text."""
        if not chunk:
            return ShimEmit()
        self._buffer += chunk
        visible: list[str] = []
        calls: list[ShimCall] = []
        failures: list[ShimFailure] = []

        while True:
            if self._in_call:
                end = _find_close_outside_string(self._buffer)
                if end < 0:
                    # Still inside a call: the payload stays buffered, and none of
                    # it is visible. Nothing to release.
                    #
                    # A close tag sitting inside an *unterminated* string is not
                    # resolvable yet — it could be a literal (the string closes
                    # later) or the real terminator of a truncated payload. That
                    # is decided in `finish`, where nothing more can arrive.
                    break
                payload = self._buffer[:end]
                self._buffer = self._buffer[end + len(CLOSE_TAG) :]
                self._in_call = False
                parsed_calls, parsed_failures = _read_payload(payload)
                calls.extend(parsed_calls)
                failures.extend(parsed_failures)
                continue

            start = self._buffer.find(OPEN_TAG)
            if start >= 0:
                visible.append(self._buffer[:start])
                self._buffer = self._buffer[start + len(OPEN_TAG) :]
                self._in_call = True
                continue

            hold = _held_suffix_len(self._buffer, OPEN_TAG)
            if hold:
                visible.append(self._buffer[: len(self._buffer) - hold])
                self._buffer = self._buffer[len(self._buffer) - hold :]
            else:
                visible.append(self._buffer)
                self._buffer = ""
            break

        return ShimEmit(
            visible="".join(visible),
            calls=tuple(calls),
            failures=tuple(failures),
        )

    def finish(self) -> ShimEmit:
        """Flush at end of stream. Releases held text; reports an open block."""
        tail = self._buffer
        self._buffer = ""
        if self._in_call:
            self._in_call = False
            # The stream is over, so a close tag that only ever appeared inside an
            # unterminated string was the real terminator after all — the payload
            # was truncated mid-value. Recovering it keeps the truncation-repair
            # path that `close_truncated` exists for, instead of discarding a call
            # the model genuinely made.
            fallback = tail.find(CLOSE_TAG)
            if fallback >= 0:
                calls, failures = _read_payload(tail[:fallback])
                rest = tail[fallback + len(CLOSE_TAG) :]
                trailing = ShimEmit()
                if rest:
                    # Run the remainder through a fresh parser rather than
                    # releasing it blind: it may itself hold a whole call, and
                    # emitting that as prose is the leak this class forbids.
                    nested = ShimStreamParser()
                    first = nested.feed(rest)
                    last = nested.finish()
                    trailing = ShimEmit(
                        visible=first.visible + last.visible,
                        calls=first.calls + last.calls,
                        failures=first.failures + last.failures,
                    )
                return ShimEmit(
                    visible=trailing.visible,
                    calls=calls + trailing.calls,
                    failures=failures + trailing.failures,
                )
            return ShimEmit(
                failures=(
                    ShimFailure(
                        raw=f"{OPEN_TAG}{tail}",
                        error=(
                            "the stream ended inside a tool call — the "
                            f"{CLOSE_TAG} was never emitted"
                        ),
                    ),
                ),
            )
        # A held suffix that never became a tag is ordinary text after all.
        return ShimEmit(visible=tail)


def _read_payload(payload: str) -> tuple[tuple[ShimCall, ...], tuple[ShimFailure, ...]]:
    """Read one block body into calls.

    A well-behaved block holds one object. A model that crams several calls into
    one block gets each balanced object read separately rather than the whole body
    failing to parse — that shape is common enough to be worth handling, and the
    alternative is telling a model that *did* ask for three things that it asked
    for nothing.
    """
    stripped = payload.strip()
    if not stripped:
        return (), (ShimFailure(raw=payload, error="the tool call block was empty"),)

    parsed = parse_arguments(stripped)
    if parsed.ok:
        call = _coerce_call(parsed.value)
        if call is not None:
            return (call,), ()
        return (), (
            ShimFailure(
                raw=payload,
                error=(
                    'the block parsed but had no "name" key — a call must be '
                    '{"name": ..., "arguments": {...}}'
                ),
            ),
        )

    # Not one clean object: try reading the balanced objects out of it. This also
    # covers the *single* call wrapped in chatter ("Here you go: {…}"), which used
    # to fail while the two-call version of the same shape parsed — an asymmetry
    # that hit the commoner case.
    objects = find_json_objects(stripped)
    if objects:
        calls: list[ShimCall] = []
        failures: list[ShimFailure] = []
        for raw in objects:
            inner = parse_arguments(raw)
            call = _coerce_call(inner.value) if inner.ok else None
            if call is not None:
                calls.append(call)
            else:
                failures.append(ShimFailure(raw=raw, error=inner.error or "not a tool call object"))
        if calls:
            return tuple(calls), tuple(failures)

    return (), (ShimFailure(raw=payload, error=parsed.error),)


def _coerce_call(obj: object) -> ShimCall | None:
    """Pull ``(name, raw_arguments)`` out of a parsed block body."""
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    if not isinstance(name, str) or not name.strip():
        return None
    arguments = obj.get("arguments")
    if arguments is None:
        arguments = obj.get("parameters")
    if arguments is None:
        arguments = obj.get("args", {})
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, sort_keys=True)
    return ShimCall(name=name.strip(), raw_arguments=raw)


# --------------------------------------------------------------------------- #
# The client wrapper
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Attempt:
    """One pass at getting a parseable call out of the model."""

    text: list[str] = field(default_factory=list)
    calls: list[ShimCall] = field(default_factory=list)
    failures: list[ShimFailure] = field(default_factory=list)
    finish: FinishReason = FinishReason.STOP
    usage: Usage = field(default_factory=Usage)
    #: Notes the wrapped client reported. Carried through rather than dropped: the
    #: inner adapter's "usage not reported by this runtime" is exactly the kind of
    #: caveat that must not disappear because a wrapper rebuilt the Completed.
    inner_notes: list[str] = field(default_factory=list)


class ShimClient:
    """Wraps a client whose model has no native tool calling.

    Presents the same interface as any other adapter, so the router, the
    accounting ledger and the loop cannot tell the difference. That is the point:
    ``native_tools`` is a fact about a model, not a branch in the call site.
    """

    def __init__(self, inner: ModelClient, *, max_repairs: int = MAX_REPAIRS) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs must be >= 0")
        self._inner = inner
        self._max_repairs = max_repairs

    def capabilities(self) -> Capabilities:
        """The inner model's capabilities, with ``native_tools`` pinned False.

        Reporting the truth here matters: the loop uses ``parallel_tools`` to
        decide whether it may dispatch a batch concurrently, and the shim is
        strictly sequential.
        """
        return replace(self._inner.capabilities(), native_tools=False, parallel_tools=False)

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        """Stream with tool calls parsed out of the text, repairing up to twice."""
        messages = req.messages
        for attempt_index in range(self._max_repairs + 1):
            shimmed = replace(
                req,
                system=build_shim_system(req.system, req.tools),
                tools=(),
                messages=messages,
            )
            attempt = _Attempt()
            parser = ShimStreamParser()

            async for delta in self._inner.stream(shimmed):
                if isinstance(delta, TextDelta):
                    emit = parser.feed(delta.text)
                    attempt.text.append(emit.visible)
                    attempt.calls.extend(emit.calls)
                    attempt.failures.extend(emit.failures)
                    if emit.visible:
                        yield TextDelta(emit.visible)
                elif isinstance(delta, ThinkingDelta):
                    # Reasoning is never scanned for calls: a model that *thinks*
                    # about calling a tool has not called it.
                    yield delta
                elif isinstance(delta, StreamReset):
                    parser = ShimStreamParser()
                    attempt = _Attempt()
                    yield delta
                elif isinstance(delta, Completed):
                    attempt.finish = delta.finish
                    attempt.usage = delta.usage
                    attempt.inner_notes.extend(delta.notes)
                    # A native-shaped call from a model we thought had none is
                    # still a call. Trust it over our own parsing.
                    for use in delta.tool_uses:
                        attempt.calls.append(
                            ShimCall(
                                name=use.name,
                                raw_arguments=json.dumps(dict(use.arguments), sort_keys=True),
                            )
                        )

            emit = parser.finish()
            attempt.text.append(emit.visible)
            attempt.calls.extend(emit.calls)
            attempt.failures.extend(emit.failures)
            if emit.visible:
                yield TextDelta(emit.visible)

            text = "".join(attempt.text)
            # Normalizing here rather than in `_complete` is what makes an
            # unparseable *argument* repairable. A block can be well-formed XML
            # holding a well-formed object whose "arguments" is still garbage
            # (`"arguments": "not an object"`); deciding to repair only on parser
            # failures would drop that call silently, and the model would look as
            # though it had asked for nothing.
            normalized = _normalize(attempt.calls)
            failures = (
                *attempt.failures,
                *(ShimFailure(raw=f.raw, error=f.error) for f in normalized.failed),
            )
            if not failures or attempt_index == self._max_repairs:
                yield _complete(attempt, text, normalized, failures, attempts_used=attempt_index)
                return

            # Repair: replay the model's own words, then the correction. Both go
            # after the stable prefix, so the retry is still a cache hit.
            failure = failures[0]
            messages = (
                *messages,
                Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
                repair_message(failure.raw, failure.error),
            )
            yield StreamReset(
                reason=f"tool call did not parse ({failure.error}); asking the model to repair"
            )


def _normalize(calls: Sequence[ShimCall]) -> NormalizedCalls:
    """Run extracted calls through the shared accumulator.

    The shim uses the *same* normalizer as every native adapter, which is why a
    local Qwen and Claude produce identical ``ToolUse`` values for the same call.
    """
    accumulator = ToolCallAccumulator()
    for index, call in enumerate(calls):
        accumulator.add_complete(index, name=call.name, arguments=call.raw_arguments)
    return accumulator.finish()


def _complete(
    attempt: _Attempt,
    text: str,
    normalized: NormalizedCalls,
    failures: Sequence[ShimFailure],
    *,
    attempts_used: int,
) -> Completed:
    """Assemble the terminal delta for one shim run."""
    notes = [*attempt.inner_notes, *normalized.notes]
    if attempts_used and not failures:
        # Only claim a repair when one actually happened. Saying "repaired after 1
        # retry" next to "gave up after 1 attempt" is a log that contradicts itself.
        notes.append(f"shim repaired the tool call after {attempts_used} retry(ies)")

    text_blocks: tuple[Text, ...] = (Text(text),) if text else ()

    if failures:
        # Repairs are exhausted. Surface it: the text the model wrote is kept so
        # the user can see what it tried, and the finish reason says this turn
        # failed rather than quietly returning "no tool calls" (which the loop
        # would read as a finished answer).
        notes.extend(f"unparseable tool call: {failure.error}" for failure in failures)
        detail = "; ".join(f.error for f in failures)
        return Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=text_blocks),
            finish=FinishReason.ERROR,
            usage=attempt.usage,
            notes=(
                *notes,
                f"gave up after {attempts_used} repair attempt(s): {detail}",
            ),
        )

    content: tuple[ContentBlock, ...] = (*text_blocks, *normalized.uses)
    message = Message(role=Role.ASSISTANT, content_blocks=content)
    finish = FinishReason.TOOL_USE if normalized.uses else attempt.finish
    return Completed(
        message=message,
        finish=finish,
        usage=attempt.usage,
        notes=tuple(notes),
    )
