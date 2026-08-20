"""One shape for tool calls, whatever the provider did.

Every provider gets tool calls wrong in its own way. This module is the single
place that knows about all of it, so no adapter carries repair logic and every
adapter produces byte-identical ``ToolUse`` values for equivalent input — which
is exactly what the golden fixture tests assert.

The shapes handled, each because a real provider does it:

* **Streaming partial JSON.** Arguments arrive as fragments across chunks and are
  only valid concatenated. We buffer per index and parse once at the end.
* **Missing ids.** Ollama, llama.cpp and several fine-tunes omit the id entirely.
  We mint one *deterministically* from the call's content — a random uuid would
  make the fixtures untestable and would change on replay.
* **Colliding ids.** Some servers reuse ``call_0`` for every call in a response,
  or repeat one id across a parallel batch. Duplicates make ``tool_use`` ↔
  ``tool_result`` pairing ambiguous, which providers reject with a 400 on the
  *next* turn — a failure that surfaces far from its cause. We disambiguate.
* **Arguments as a string.** The OpenAI wire format always does this; weak models
  additionally wrap it in fences or leave trailing commas (see
  :mod:`ronin.providers.jsonargs`).
* **Multiple calls in one text block.** Handled upstream by the shim, which feeds
  each extracted object in as its own index.

A call whose arguments cannot be recovered is *not* dropped. Dropping it makes
the model look like it said nothing, and it retries the same broken call forever.
It is kept, marked, and the caller decides — see :attr:`NormalizedCalls.failed`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.types import ToolUse
from .jsonargs import ArgsParse, parse_arguments

#: Prefix for ids we mint. Recognizable in a transcript, so "where did this id
#: come from" is answerable without digging through provider logs.
SYNTHETIC_PREFIX = "ron"


@dataclass(frozen=True, slots=True)
class FailedCall:
    """A call the model made that we could not turn into arguments.

    Kept as a value so the caller can feed the model a repair message quoting
    ``raw`` verbatim. ``use`` is still populated (with empty arguments) so the
    transcript can pair a ``tool_result`` to it and stay sendable.
    """

    use: ToolUse
    raw: str
    error: str


@dataclass(frozen=True, slots=True)
class NormalizedCalls:
    """The result of normalizing one response's tool calls."""

    uses: tuple[ToolUse, ...] = ()
    failed: tuple[FailedCall, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failed


def synthetic_id(index: int, name: str, arguments: str) -> str:
    """A deterministic id for a call the provider did not name.

    Derived from content rather than a counter so that replaying the same stream
    produces the same transcript — the property the golden tests rest on. Two
    genuinely identical calls in one response would collide here; the accumulator
    resolves that afterwards, where it can see the whole batch.
    """
    digest = hashlib.sha256(f"{index}\x00{name}\x00{arguments}".encode()).hexdigest()
    return f"{SYNTHETIC_PREFIX}_{digest[:12]}"


@dataclass(slots=True)
class _Partial:
    """Fragments of one tool call as they arrive."""

    index: int
    id: str | None = None
    name: str | None = None
    fragments: list[str] = field(default_factory=list)

    @property
    def raw_arguments(self) -> str:
        return "".join(self.fragments)


class ToolCallAccumulator:
    """Collects :class:`~ronin.providers.types.ToolCallDelta` fragments into calls.

    Mutable by design — it is a stream accumulator — but it is the *only* mutable
    thing in the provider layer's hot path, and :meth:`finish` returns frozen
    values. Ordering is by first appearance of an index, not by index value, so a
    provider that numbers calls out of order still yields them in the order the
    model actually emitted them.
    """

    def __init__(self) -> None:
        self._partials: dict[int, _Partial] = {}
        self._order: list[int] = []

    def add(
        self,
        index: int,
        *,
        call_id: str | None = None,
        name: str | None = None,
        arguments_fragment: str = "",
    ) -> None:
        """Fold one fragment in. Later non-empty ``id``/``name`` values win."""
        partial = self._partials.get(index)
        if partial is None:
            partial = _Partial(index=index)
            self._partials[index] = partial
            self._order.append(index)
        if call_id:
            partial.id = call_id
        if name:
            # Some servers stream the name in pieces too (rare, but Kimi does it
            # when a tool name is long). Concatenate rather than overwrite only
            # when the new piece is not already a prefix-complete name.
            partial.name = name if partial.name is None else _merge_name(partial.name, name)
        if arguments_fragment:
            partial.fragments.append(arguments_fragment)

    def add_complete(
        self,
        index: int,
        *,
        name: str,
        arguments: str | Mapping[str, Any],
        call_id: str | None = None,
    ) -> None:
        """Fold in a call that arrived whole (non-streaming responses, the shim)."""
        fragment = arguments if isinstance(arguments, str) else json.dumps(arguments)
        self.add(index, call_id=call_id, name=name, arguments_fragment=fragment)

    @property
    def empty(self) -> bool:
        return not self._partials

    def finish(self) -> NormalizedCalls:
        """Parse every accumulated call and resolve ids. Never raises."""
        uses: list[ToolUse] = []
        failed: list[FailedCall] = []
        notes: list[str] = []
        seen_ids: set[str] = set()

        for index in self._order:
            partial = self._partials[index]
            raw = partial.raw_arguments
            name = (partial.name or "").strip()
            if not name:
                # A call with no name is unusable — there is nothing to route it
                # to and no repair message worth sending. This is the one case we
                # drop, and we say so loudly.
                notes.append(f"dropped call at index {index}: provider sent no tool name")
                continue

            call_id = partial.id or ""
            if not call_id:
                call_id = synthetic_id(index, name, raw)
                notes.append(f"minted id {call_id} for {name!r} (provider sent none)")

            resolved = _dedupe(call_id, seen_ids)
            if resolved != call_id:
                notes.append(f"id {call_id!r} collided; using {resolved!r} for {name!r}")
            seen_ids.add(resolved)

            parsed = parse_arguments(raw)
            notes.extend(f"{name}: {repair}" for repair in parsed.repairs)
            if parsed.ok:
                uses.append(ToolUse(id=resolved, name=name, arguments=parsed.value))
            else:
                failed.append(
                    FailedCall(
                        use=ToolUse(id=resolved, name=name, arguments={}),
                        raw=parsed.raw,
                        error=parsed.error,
                    )
                )

        return NormalizedCalls(uses=tuple(uses), failed=tuple(failed), notes=tuple(notes))


def _merge_name(existing: str, incoming: str) -> str:
    """Combine two name fragments without duplicating an already-complete name."""
    if incoming == existing or existing.endswith(incoming):
        return existing
    if incoming.startswith(existing):
        return incoming
    return existing + incoming


def _dedupe(call_id: str, taken: set[str]) -> str:
    """Return ``call_id`` if free, else a deterministic distinct variant."""
    if call_id not in taken:
        return call_id
    suffix = 2
    while f"{call_id}#{suffix}" in taken:
        suffix += 1
    return f"{call_id}#{suffix}"


def normalize_one(
    *,
    index: int = 0,
    name: str,
    arguments: object,
    call_id: str | None = None,
) -> tuple[ToolUse | None, ArgsParse]:
    """Normalize a single already-complete call. Convenience for non-streaming paths."""
    parsed = parse_arguments(arguments)
    resolved = call_id or synthetic_id(index, name, arguments if isinstance(arguments, str) else "")
    if not parsed.ok:
        return None, parsed
    return ToolUse(id=resolved, name=name, arguments=parsed.value), parsed
