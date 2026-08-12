#!/usr/bin/env python3
"""The SDK, end to end, with no model and no network: ``python examples/sdk_quickstart.py``.

Everything here imports from ``ronin`` and nothing else — that is the point of the file.
If a name used below has to be reached through ``ronin.cli.sdk`` or ``ronin.core.types``
instead, the public surface has a hole in it and this script stops running.

A scripted model stands in for a provider so this costs nothing and works offline. Swap
the two ``router=``/``model=`` lines for a real ``models.toml`` and the rest is unchanged:

    async with await Agent.open(".") as agent:
        result = await agent.run("what does src/ronin/core/loop.py do?")

Run it and you will see, in order: the two call shapes over one implementation, a
caller's own tool being called by the agent, and the events a consumer switches on.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ronin import (
    Agent,
    AgentConfig,
    AgentResult,
    Event,
    TextDelta,
    Tool,
    ToolContext,
    ToolEnd,
    ToolResult,
    ToolSpec,
    ToolStart,
    ToolUse,
)

# The one import that is *not* from the package root, and it is deliberate: a scripted
# provider is test scaffolding, not public API. A real caller writes a models.toml.
from ronin.providers.router import Role, Router, RouterConfig

RULE = "─" * 78


class Weather(Tool):
    """A caller's own tool. Sixteen lines, and the agent can call it like any builtin.

    This is what ``AgentConfig.tools`` is for. It is gated exactly like ``read`` or
    ``bash``: same hooks, same taint tracking, same output budget — a tool an embedder
    brings is not a tool that escapes the rules.
    """

    def __init__(self) -> None:
        self.asked: list[str] = []

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="weather",
            description=(
                "The weather in a city. WHEN TO USE: when the user asks about weather. "
                "WHEN NOT TO USE: for anything else. EXAMPLE: weather(city='Kyoto')"
            ),
            json_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        city = str(arguments.get("city", ""))
        self.asked.append(city)
        return ToolResult(ok=True, content=f"{city}: 18°C, raining")


def scripted_router(reply: str) -> Router:
    """A provider that answers with one line. Keeps this script offline and free.

    The only part a real caller replaces: point ``models.toml`` at a provider and drop
    the ``router=`` argument entirely.
    """
    from ronin.core.types import Message, Text
    from ronin.core.types import Role as MessageRole
    from ronin.providers.base import ModelRequest
    from ronin.providers.router import ModelSpec
    from ronin.providers.types import CONSERVATIVE, Capabilities, Completed, Usage
    from ronin.providers.types import TextDelta as ProviderText

    class Scripted:
        """Satisfies ``providers.base.ModelClient``: stream deltas, declare capabilities."""

        def capabilities(self) -> Capabilities:
            return CONSERVATIVE

        def stream(self, req: ModelRequest) -> Any:
            async def deltas() -> Any:
                yield ProviderText(reply)
                yield Completed(
                    message=Message(
                        role=MessageRole.ASSISTANT, content_blocks=(Text(reply),)
                    ),
                    usage=Usage(input_tokens=8, output_tokens=8),
                )

            return deltas()

    spec = ModelSpec(name="scripted", provider="stub", model="scripted-1")
    config = RouterConfig(models={"scripted": spec}, roles={Role.MAIN: "scripted"})
    return Router(config, build=lambda _spec, **_kwargs: Scripted())


def describe(event: Event) -> str:
    """What a consumer does with the union: switch on the member, read its fields."""
    if isinstance(event, TextDelta):
        return f"text {event.text[:40]!r}"
    if isinstance(event, ToolStart):
        return f"tool  {event.name}({dict(event.arguments)})"
    if isinstance(event, ToolEnd):
        return f"  →   {event.result.content[:40]}"
    return type(event).__name__


async def main() -> int:
    print(f"{RULE}\n1. the two shapes, over one implementation\n{RULE}")
    print("`await agent.run(p)`     → AgentResult (folded)")
    print("`async for e in run(p)`  → typed events")
    print("`Agent(config)`          → synchronous, opens on first use")
    print("`await Agent.open(path)` → assembled up front\n")

    with TemporaryDirectory() as directory:
        workspace = Path(directory)
        weather = Weather()
        config = AgentConfig(
            path=workspace,
            router=scripted_router("Kyoto is rainy today."),
            environ={},
            record=False,
            connect_mcp=False,
            tools=[weather],
        )

        # Synchronous construction: no await, no event loop needed, nothing read yet.
        agent = Agent(config)
        print("built from a config; opened:", agent._runtime is not None)  # noqa: SLF001

        async with agent:
            await agent.ready()
            print("after ready();  opened:", agent._runtime is not None)  # noqa: SLF001

            names = sorted(spec.name for spec in agent.runtime.registry.specs())
            print(f"\n{RULE}\n2. a caller's tool, in the registry the model sees\n{RULE}")
            print(f"{len(names)} tools, including the one this script defined:")
            print("  " + ", ".join(names))
            assert "weather" in names, "the caller's tool did not reach the registry"

            result = await agent.runtime.registry.execute(
                ToolUse(id="demo-1", name="weather", arguments={"city": "Kyoto"})
            )
            print(f"\ncalled through the *gated* registry: {result.content}")
            print(f"the tool recorded: {weather.asked}")

            print(f"\n{RULE}\n3. iterating one run for typed events\n{RULE}")
            events: list[Event] = []
            async for event in agent.run("what is the weather in Kyoto?"):
                events.append(event)
                print(f"  {describe(event)}")

            print(f"\n{RULE}\n4. the same run, awaited\n{RULE}")
            folded = await agent.run("say that again")
            assert isinstance(folded, AgentResult)
            print(f"text:      {folded.text!r}")
            print(f"exit code: {folded.exit_code} (0 done, 1 error, 2 needs approval)")
            print(f"events:    {len(folded.events)}")
            print(f"ok:        {folded.ok}")

    print(f"\n{RULE}")
    print("every name above came from `ronin` itself, except the scripted provider —")
    print("which is test scaffolding a real caller replaces with a models.toml.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
