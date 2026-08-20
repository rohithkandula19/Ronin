"""Build requests so the prompt cache actually hits, and measure whether it did.

Prompt caching is a prefix match: the provider caches the longest leading span of
the request it has seen before, and the match ends at the *first* byte that
differs. Which means the expensive part is not enabling the cache — it is never
reordering the front of the prompt.

So the stable prefix is a value, not a convention. :class:`StablePrefix` renders
to bytes deterministically, in one fixed order (system → tools → repo map), and
:func:`assemble` is the only way to build a request from one. Code that wants to
add context has to decide whether it is stable or not, which is the decision that
was being made accidentally before.

Ordering rationale, front to back by how often each part changes:

1. **System prompt** — changes when the product changes.
2. **Tool specs** — changes when the toolset changes, which is per session.
3. **Repo map** — changes when the repo changes, which is per commit.

Anything that changes per *turn* is a message, and messages come after the marker.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from ..core.types import Message, ToolSpec
from .types import Capabilities, ModelRequest, Usage


@dataclass(frozen=True, slots=True)
class StablePrefix:
    """The part of a prompt that must stay byte-identical across turns.

    Frozen, and rendered by exactly one function, because "stable" is a promise
    that cannot be kept by convention. The ``fingerprint`` makes a broken promise
    visible: log it per request and a changed value on a turn that should have been
    cacheable is the bug, no guessing required.
    """

    system: str = ""
    tools: tuple[ToolSpec, ...] = ()
    repo_map: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.tools, tuple):
            raise TypeError("StablePrefix.tools must be a tuple (frozen)")

    def render_system(self) -> str:
        """The system text, tools excluded (adapters send tools structurally)."""
        return self.system

    def render_context(self) -> str:
        """The non-system stable context, in fixed order."""
        return self.repo_map

    def fingerprint(self) -> str:
        """A stable hash of everything in the prefix, including tool order.

        Tool *order* is part of the identity on purpose: reordering the tool list
        changes the rendered request and breaks the cache, so a fingerprint that
        ignored order would report a hit that never happened.
        """
        payload = json.dumps(
            {
                "system": self.system,
                "repo_map": self.repo_map,
                "tools": [
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "schema": spec.json_schema,
                    }
                    for spec in self.tools
                ],
            },
            sort_keys=True,
            default=repr,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def assemble(
    prefix: StablePrefix,
    messages: Sequence[Message],
    *,
    model: str,
    capabilities: Capabilities,
    max_tokens: int = 4096,
    temperature: float | None = None,
    thinking_budget: int = 0,
) -> ModelRequest:
    """Build a request whose stable part is in the fixed, cacheable order.

    ``cache_marker`` is set to the index of the last stable block when the model
    supports caching and there is enough prefix to be worth caching, and left at
    ``-1`` otherwise. Adapters honour it; nobody else needs to know how a given
    provider spells "cache this".
    """
    stable_blocks = _count_stable_blocks(prefix)
    marker = -1
    if capabilities.prompt_cache and stable_blocks:
        marker = stable_blocks - 1
    return ModelRequest(
        model=model,
        system=prefix.render_system(),
        prefix=prefix.render_context(),
        tools=prefix.tools,
        messages=tuple(messages),
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_budget=thinking_budget if capabilities.thinking else 0,
        cache_marker=marker,
        metadata={"prefix_fingerprint": prefix.fingerprint()},
    )


def _count_stable_blocks(prefix: StablePrefix) -> int:
    """How many blocks the prefix renders to. Tools count as one block."""
    count = 0
    if prefix.system:
        count += 1
    if prefix.repo_map:
        count += 1
    if prefix.tools:
        count += 1
    return count


def extend(req: ModelRequest, messages: Sequence[Message]) -> ModelRequest:
    """Append turns without touching the stable prefix.

    The only supported way to continue a conversation. Rebuilding the request from
    scratch is what silently reorders a prefix; this cannot, because it does not
    have the pieces.
    """
    return replace(req, messages=(*req.messages, *messages))


# --------------------------------------------------------------------------- #
# Measuring
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class CacheStats:
    """Rolling cache-hit measurement for one session.

    Measured in *tokens*, not requests: a request whose 40-token tail missed is
    nothing like one that missed the whole 30k prefix, and a per-request rate
    would call them the same thing.
    """

    read_tokens: int = 0
    written_tokens: int = 0
    uncached_input_tokens: int = 0
    requests: int = 0
    #: Fingerprints seen, in first-seen order. A prefix that appears once and
    #: never again is a cache that was written and never read — the most common
    #: way caching silently costs money instead of saving it.
    fingerprints: dict[str, int] = field(default_factory=dict)

    def record(self, usage: Usage, *, fingerprint: str = "") -> None:
        self.requests += 1
        self.read_tokens += usage.cache_read_tokens
        self.written_tokens += usage.cache_write_tokens
        self.uncached_input_tokens += usage.input_tokens
        if fingerprint:
            self.fingerprints[fingerprint] = self.fingerprints.get(fingerprint, 0) + 1

    @property
    def hit_rate(self) -> float:
        """Cached input tokens as a fraction of all input tokens.

        Returns 0.0 with no data rather than raising: a session that has not made
        a request has not missed the cache either, and a caller charting this
        should see a floor, not an exception.
        """
        total = self.read_tokens + self.written_tokens + self.uncached_input_tokens
        return self.read_tokens / total if total else 0.0

    @property
    def write_only_prefixes(self) -> tuple[str, ...]:
        """Prefixes seen exactly once — written to the cache, never read back."""
        return tuple(fp for fp, count in self.fingerprints.items() if count == 1)

    def summary(self) -> str:
        """One line for a log or the demo output."""
        return (
            f"cache: {self.hit_rate:.0%} hit "
            f"({self.read_tokens:,} read / {self.written_tokens:,} written / "
            f"{self.uncached_input_tokens:,} uncached) over {self.requests} request(s)"
        )
