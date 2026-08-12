"""Adapt this layer to the seam ``ronin.core.loop`` was already written against.

``ronin.core.protocols.ModelClient`` takes ``(system, messages, tools)`` and yields
``TextChunk``/``ResetChunk``/``FinalMessage``. This package's client takes a
:class:`~ronin.providers.types.ModelRequest` and yields
:data:`~ronin.providers.types.ModelDelta`. Both are deliberate, and they are not
the same interface:

* The loop's seam is *narrow on purpose*. It has no opinion about temperature,
  caching, or thinking budgets, because a loop that knew about those would be a
  loop that had to change when a provider did.
* The provider seam needs a request *object*, because the cacheable prefix, the
  cache marker and the sampling knobs travel together and threading six keyword
  arguments through every adapter is how one gets dropped.

So this module is a translation, forty lines of it, rather than a rewrite of
either side. That is the honest trade: one small adapter, versus editing the loop
and its 93 tests to carry fields the loop does not use.

The translation is lossy in exactly one direction and that is fine: the loop never
sets a temperature, so :class:`LoopClient` supplies the defaults its config chose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from ..core import protocols as core
from ..core.types import Message, ToolSpec
from .assembly import CacheStats, StablePrefix, assemble
from .base import ModelClient
from .types import (
    Completed,
    FinishReason,
    ModelRequest,
    StreamReset,
    TextDelta,
    ThinkingDelta,
)


def _error_text(delta: Completed) -> str:
    """The sentence a consumer shows when a provider reported a failed turn.

    Prefers the notes, because that is where the shim puts "gave up after 2 repair
    attempt(s): …" — the part a user can act on. Falls back to naming the finish
    reason so the error is never an empty string, which
    :class:`~ronin.core.types.Error` rejects anyway.
    """
    detail = "; ".join(note for note in delta.notes if note)
    return detail or "the provider ended this turn with an error"


class LoopClient:
    """Presents a provider-layer client as the loop's ``ModelClient``.

    Holds the sampling configuration the loop has no opinion about, and keeps a
    :class:`~ronin.providers.assembly.CacheStats` so the caller can read the hit
    rate after a turn without the loop having to carry it.
    """

    def __init__(
        self,
        inner: ModelClient,
        *,
        model: str,
        repo_map: str = "",
        max_tokens: int = 4096,
        temperature: float | None = None,
        thinking_budget: int = 0,
    ) -> None:
        self._inner = inner
        self._model = model
        self._repo_map = repo_map
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._thinking_budget = thinking_budget
        self.cache_stats = CacheStats()
        #: The last request built, for logging and for the demo's output. Kept
        #: rather than recomputed so what is reported is what was actually sent.
        self.last_request: ModelRequest | None = None

    def with_model(self, inner: ModelClient, *, model: str) -> LoopClient:
        """This sampling configuration, pointed at a different provider client.

        What ``/model`` switches with. Rebuilding a :class:`LoopClient` at the call site
        would mean re-deriving the repo map and the token ceiling somewhere else, and the
        copy that drifts is always the one furthest from the assembly that owns it.

        A fresh :class:`CacheStats` comes with it, because prompt-cache hit rate is a
        property of one model's conversation: carrying the old counters over would report
        hits against a cache the new model never had.
        """
        return LoopClient(
            inner,
            model=model,
            repo_map=self._repo_map,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            thinking_budget=self._thinking_budget,
        )

    def build_request(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> ModelRequest:
        """Assemble the request the loop's arguments imply.

        Goes through :func:`~ronin.providers.assembly.assemble` rather than
        constructing a ``ModelRequest`` directly, so the loop path gets the same
        stable-prefix ordering and cache marker as every other caller.
        """
        prefix = StablePrefix(system=system, tools=tuple(tools), repo_map=self._repo_map)
        return assemble(
            prefix,
            messages,
            model=self._model,
            capabilities=self._inner.capabilities(),
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            thinking_budget=self._thinking_budget,
        )

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[core.ModelChunk]:
        """Translate one provider stream into the loop's chunk vocabulary."""
        req = self.build_request(system=system, messages=messages, tools=tools)
        self.last_request = req
        fingerprint = str(req.metadata.get("prefix_fingerprint", ""))

        async for delta in self._inner.stream(req):
            if isinstance(delta, TextDelta):
                yield core.TextChunk(delta.text)
            elif isinstance(delta, ThinkingDelta):
                # The loop renders thinking as text tagged `thinking=True`; it does
                # not need the signature, which stays on the assembled Message.
                yield core.TextChunk(delta.text, thinking=True)
            elif isinstance(delta, StreamReset):
                yield core.ResetChunk(delta.reason)
            elif isinstance(delta, Completed):
                self.cache_stats.record(delta.usage, fingerprint=fingerprint)
                yield core.FinalMessage(
                    message=delta.message,
                    input_tokens=delta.usage.input_tokens + delta.usage.cache_read_tokens,
                    output_tokens=delta.usage.output_tokens,
                    cost_usd=delta.usage.cost_usd,
                    # `finish` and `notes` used to stop here. That made this
                    # translation lossy in a second direction the docstring above
                    # did not claim: a shim that exhausted its repair budget set
                    # `FinishReason.ERROR`, and the turn still reached the loop as
                    # ordinary prose with no tool calls — indistinguishable from a
                    # model that simply answered.
                    error=_error_text(delta) if delta.finish is FinishReason.ERROR else "",
                    notes=delta.notes,
                )


def _conforms(client: LoopClient) -> core.ModelClient:
    """Static proof that the bridge satisfies the loop's seam."""
    return client
