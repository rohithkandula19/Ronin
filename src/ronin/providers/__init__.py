"""Provider-agnostic model access: swapping models is config, not code.

The shape of this package, in the order you meet it:

* :mod:`~ronin.providers.types` — ``ModelRequest`` in, ``ModelDelta`` out, plus
  ``Capabilities``, which is the only thing about a specific model that the rest of
  the system is allowed to know.
* :mod:`~ronin.providers.base` — the ``ModelClient`` protocol, SSE plumbing, and
  the ``Transport`` seam that keeps every adapter testable offline.
* Four adapters: :mod:`~ronin.providers.anthropic`,
  :mod:`~ronin.providers.openai_compat` (which covers eight hosts and, via
  ``MoonshotClient``, Kimi K2's quirks), and :mod:`~ronin.providers.mlx_local`.
* :mod:`~ronin.providers.normalize` and :mod:`~ronin.providers.jsonargs` — one
  ``ToolUse`` shape out of every provider's idea of a tool call.
* :mod:`~ronin.providers.shim` — tool calling for models that have none.
* :mod:`~ronin.providers.router` and :mod:`~ronin.providers.registry` — roles map
  to models, models map to clients.
* :mod:`~ronin.providers.assembly` and :mod:`~ronin.providers.accounting` — cache
  the prefix, then prove it worked and say what it cost.
* :mod:`~ronin.providers.bridge` — presents all of it as the seam
  ``ronin.core.loop`` already expects.

Nothing here imports from ``ronin.core`` except the shared *types*, and nothing in
``ronin.core`` imports this package at all — the loop receives a client, it never
looks one up.
"""
from __future__ import annotations

from .accounting import Ledger, Totals, price
from .anthropic import AnthropicClient
from .assembly import CacheStats, StablePrefix, assemble, extend
from .base import HttpTransport, ModelClient, SSEReader, Transport
from .bridge import LoopClient
from .jsonargs import ArgsParse, parse_arguments
from .mlx_local import MLXClient
from .normalize import NormalizedCalls, ToolCallAccumulator
from .openai_compat import KNOWN_BASE_URLS, MoonshotClient, OpenAICompatClient
from .registry import ADAPTERS, build_client, describe
from .router import ModelSpec, Role, Router, RouterConfig, load_config, parse_config
from .shim import ShimClient, ShimStreamParser, build_shim_system, render_tool_specs
from .types import (
    CONSERVATIVE,
    Capabilities,
    Completed,
    FinishReason,
    ModelDelta,
    ModelRequest,
    ProviderError,
    StreamReset,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    Usage,
)

__all__ = [
    "ADAPTERS",
    "CONSERVATIVE",
    "KNOWN_BASE_URLS",
    "AnthropicClient",
    "ArgsParse",
    "CacheStats",
    "Capabilities",
    "Completed",
    "FinishReason",
    "HttpTransport",
    "Ledger",
    "LoopClient",
    "MLXClient",
    "ModelClient",
    "ModelDelta",
    "ModelRequest",
    "ModelSpec",
    "MoonshotClient",
    "NormalizedCalls",
    "OpenAICompatClient",
    "ProviderError",
    "Role",
    "Router",
    "RouterConfig",
    "SSEReader",
    "ShimClient",
    "ShimStreamParser",
    "StablePrefix",
    "StreamReset",
    "TextDelta",
    "ThinkingDelta",
    "ToolCallAccumulator",
    "ToolCallDelta",
    "Totals",
    "Transport",
    "Usage",
    "assemble",
    "build_client",
    "build_shim_system",
    "describe",
    "extend",
    "load_config",
    "parse_arguments",
    "parse_config",
    "price",
    "render_tool_specs",
]
