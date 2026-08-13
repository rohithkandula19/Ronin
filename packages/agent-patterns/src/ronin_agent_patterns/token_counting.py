"""Token-counting contracts shared by the context and provider layers.

Native provider counters are preferable because they see the rendered wire
payload, including tool schemas and provider-added framing.  They can still
vary slightly from billed usage, so ``native`` means provider-reported rather
than a promise about a later completion request.

Providers without a counter use the deliberately conservative UTF-8 fallback.
It is an *estimate*, never a context-window guarantee: callers retain an output
reserve and handle a provider context-limit response as authoritative.
"""
from __future__ import annotations

import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CountKind = Literal["native", "tokenizer", "estimated"]


class TokenCount(BaseModel):
    """A count plus the authority of the mechanism that produced it."""

    model_config = ConfigDict(frozen=True)

    tokens: int = Field(ge=0)
    kind: CountKind
    method: str

    @property
    def is_estimate(self) -> bool:
        return self.kind == "estimated"


def estimate_text_tokens(text: str) -> TokenCount:
    """Return Ronin's documented fallback for un-tokenizable text.

    The estimate is ``ceil(UTF-8 bytes / 3) + 4``.  Three bytes per token is
    intentionally more conservative than the common English ``chars / 4`` rule
    and behaves better for non-ASCII content.  It is still not a model
    tokenizer, so callers must label it ``estimated`` and reserve capacity.
    """
    if not text:
        return TokenCount(tokens=0, kind="estimated", method="utf8-bytes-div-3-plus-4")
    return TokenCount(
        tokens=math.ceil(len(text.encode("utf-8")) / 3) + 4,
        kind="estimated",
        method="utf8-bytes-div-3-plus-4",
    )


def estimate_request_tokens(
    *, system: str, messages: list[Any], tools: list[Any],
) -> TokenCount:
    """Estimate a complete neutral request when no native counter is available.

    Message and tool envelopes are included before applying the text fallback;
    this makes the estimate useful for tool-heavy local and custom endpoints,
    while the result remains explicitly non-authoritative.
    """
    payload = {
        "system": system,
        "messages": [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in messages
        ],
        "tools": [
            (
                {
                    "name": item.name,
                    "description": item.description,
                    "input_schema": item.input_schema,
                }
                if all(hasattr(item, attribute) for attribute in ("name", "description", "input_schema"))
                else (item.model_dump() if hasattr(item, "model_dump") else item)
            )
            for item in tools
        ],
    }
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text_count = estimate_text_tokens(rendered)
    # Provider chat envelopes, alternating roles, and function-call delimiters
    # are not visible in the JSON string.  This small fixed overhead is cautious
    # enough for pre-compaction; a provider limit error still wins.
    overhead = 4 * len(messages) + 8 * len(tools)
    return TokenCount(
        tokens=text_count.tokens + overhead,
        kind="estimated",
        method="utf8-request-estimate-with-envelope-overhead",
    )
