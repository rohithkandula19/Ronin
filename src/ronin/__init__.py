"""Ronin — a masterless, terminal-native, provider-agnostic coding agent.

The programmatic entry point, in the two shapes callers reach for::

    import asyncio
    from ronin import Agent, AgentConfig

    async def main() -> None:
        # Await it for the answer...
        async with await Agent.open(".") as agent:
            result = await agent.run("what does core/loop.py do?")
            print(result.text, result.exit_code)

        # ...or iterate the same run for typed events.
        agent = Agent(AgentConfig(path="."))
        async for event in agent.run("fix the failing test"):
            print(type(event).__name__)
        await agent.aclose()

    asyncio.run(main())

**Every name here is resolved lazily**, through the module ``__getattr__`` in PEP 562.
That is not a micro-optimisation: importing :class:`~ronin.cli.sdk.Agent` pulls in the
whole application layer — every provider adapter, the tool registry, the MCP client —
and this package is also imported by things that want none of it. ``import ronin`` on
its own therefore costs nothing, and ``from ronin import Agent`` costs exactly what it
needs the moment it is written. The alternative, eager imports at the top of this file,
would make ``python -c "import ronin"`` load a few hundred modules to print nothing.

The lazy map is checked by a test: every name in :data:`__all__` must resolve, so this
file cannot promise a name that has been moved or renamed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time only, for type checkers
    from .cli.sdk import Agent, AgentConfig, AgentResult, Run
    from .core.types import (
        ApprovalDecision,
        ApprovalRequest,
        Budget,
        Compaction,
        Error,
        Event,
        Mode,
        StreamReset,
        TextDelta,
        ToolEnd,
        ToolResult,
        ToolSpec,
        ToolStart,
        ToolUse,
        TurnEnd,
        TurnStart,
        VerifyResult,
    )
    from .tools.base import MAX_RESULT_CHARS, Tool, ToolContext

#: Where each exported name actually lives. Data rather than a chain of ``if`` branches,
#: so the test that resolves every one of them is a loop and not a list to maintain
#: twice.
_EXPORTS: dict[str, str] = {
    "Agent": "ronin.cli.sdk",
    "AgentConfig": "ronin.cli.sdk",
    "AgentResult": "ronin.cli.sdk",
    "Run": "ronin.cli.sdk",
    # The event union and its members. A consumer of `Agent.run` switches on these, so
    # importing them from the same place as `Agent` is the difference between the SDK
    # being usable from one import and sending people into `ronin.core.types`.
    "ApprovalDecision": "ronin.core.types",
    "ApprovalRequest": "ronin.core.types",
    "Budget": "ronin.core.types",
    "Compaction": "ronin.core.types",
    "Error": "ronin.core.types",
    "Event": "ronin.core.types",
    "Mode": "ronin.core.types",
    "StreamReset": "ronin.core.types",
    "TextDelta": "ronin.core.types",
    "ToolEnd": "ronin.core.types",
    "ToolResult": "ronin.core.types",
    "ToolSpec": "ronin.core.types",
    "ToolStart": "ronin.core.types",
    "ToolUse": "ronin.core.types",
    "TurnEnd": "ronin.core.types",
    "TurnStart": "ronin.core.types",
    "VerifyResult": "ronin.core.types",
    # Writing a tool of your own needs these three, and `AgentConfig.tools` is a
    # documented door — so they belong on the same surface as the door. Found by
    # `examples/sdk_quickstart.py`, which could not define a tool without them.
    "Tool": "ronin.tools.base",
    "ToolContext": "ronin.tools.base",
    "MAX_RESULT_CHARS": "ronin.tools.base",
}


def __getattr__(name: str) -> Any:
    """Resolve an exported name on first use (PEP 562)."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'ronin' has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module), name)


def __dir__() -> list[str]:
    """So ``dir(ronin)`` and tab-completion show what is actually available."""
    return sorted(_EXPORTS)


__all__ = [
    "MAX_RESULT_CHARS",
    "Agent",
    "AgentConfig",
    "AgentResult",
    "ApprovalDecision",
    "ApprovalRequest",
    "Budget",
    "Compaction",
    "Error",
    "Event",
    "Mode",
    "Run",
    "StreamReset",
    "TextDelta",
    "Tool",
    "ToolContext",
    "ToolEnd",
    "ToolResult",
    "ToolSpec",
    "ToolStart",
    "ToolUse",
    "TurnEnd",
    "TurnStart",
    "VerifyResult",
]
