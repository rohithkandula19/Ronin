"""The registry the loop is handed: name → tool, plus the assembly of a full set.

Satisfies ``ronin.core.protocols.ToolRegistry``, which is three methods:
``specs()``, ``get(name)`` and ``execute(use)``. That is deliberately the whole
surface — the loop needs to describe tools to a model, look one up when the model
names it, and run it. Anything else it might want to know would be the loop knowing
about tools, which is the coupling the architecture exists to prevent.

``build_registry`` is where a session's toolset is decided. Note what it takes: a
context, a shell session, and *optional* runners for subagents and the network. A
registry with no network runner simply has no network tools, which is how an offline
or restricted session is configured — by not passing something, rather than by a flag
that some code path might forget to check.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace

from ..core.types import ToolResult, ToolSpec, ToolUse
from .base import Tool, ToolContext
from .files import EditTool, MultiEditTool, ReadTool, WriteTool
from .net import Clock, Extractor, Fetcher, Searcher, WebFetchTool, WebSearchTool
from .search import GlobTool, GrepTool, LsTool
from .shell import BashOutputTool, BashTool, ShellSession
from .task import BUILTIN_SUBAGENTS, SubagentRunner, SubagentType, TaskTool, TodoWriteTool


class ToolRegistry:
    """A named set of tools, with the session context bound in.

    Holds the ``ToolContext`` so ``execute`` needs only the ``ToolUse`` the model
    produced — which is exactly what the loop has, and nothing more.
    """

    def __init__(self, tools: Iterable[Tool], ctx: ToolContext) -> None:
        self._tools: dict[str, Tool] = {}
        self._specs: list[ToolSpec] = []
        for tool in tools:
            spec = tool.spec()  # validates the description while we are here
            if spec.name in self._tools:
                raise ValueError(f"duplicate tool name {spec.name!r}")
            self._tools[spec.name] = tool
            self._specs.append(spec)
        self.ctx = ctx

    def specs(self) -> Sequence[ToolSpec]:
        return tuple(self._specs)

    def get(self, name: str) -> ToolSpec | None:
        tool = self._tools.get(name)
        return tool.spec() if tool is not None else None

    def tool(self, name: str) -> Tool | None:
        """The tool object itself. Not part of the loop's protocol; used by tests."""
        return self._tools.get(name)

    def tools(self) -> tuple[Tool, ...]:
        """Every tool, in registration order. For re-assembling a wider registry."""
        return tuple(self._tools.values())

    async def execute(self, use: ToolUse) -> ToolResult:
        """Run one call. Returns a result; a hallucinated name is a value, not a raise."""
        return await self._execute(use, None)

    async def execute_streaming(self, use: ToolUse, on_output: Callable[[str], None]) -> ToolResult:
        """Run one call, giving the tool somewhere to post output as it goes.

        Satisfies ``core.protocols.StreamingToolRegistry``. Only tools that choose to
        write to ``ctx.on_output`` stream; every other tool behaves identically to
        :meth:`execute`.
        """
        return await self._execute(use, on_output)

    async def _execute(self, use: ToolUse, on_output: Callable[[str], None] | None) -> ToolResult:
        tool = self._tools.get(use.name)
        if tool is None:
            known = ", ".join(sorted(self._tools))
            return ToolResult(
                ok=False,
                error=(f"there is no tool called {use.name!r}. Available tools: {known}."),
            )
        # Bound onto a *copy* of the context rather than the shared one, so two
        # concurrent calls cannot post chunks to each other's line. The copy is shallow
        # on purpose: `read_files` stays the same set object, so the `write` guard still
        # sees everything that has been read this session.
        ctx = self.ctx if on_output is None else replace(self.ctx, on_output=on_output)
        return await tool.execute(use.arguments, ctx)

    def subset(self, names: Sequence[str], *, ctx: ToolContext | None = None) -> ToolRegistry:
        """A registry with only ``names``. How a subagent gets a narrower toolset.

        Silently skipping an unknown name would give a subagent fewer tools than its
        type declared, and the failure would look like the model being unhelpful
        rather than a typo in a config.

        ``ctx`` defaults to the parent's, but a *subagent* is given its own — sharing
        one would mean a file the child read counts as "seen" for the parent's
        ``write`` guard, quietly weakening the rule that prevents most destructive
        edits. The tools themselves are shared: they are stateless, and the state
        that matters lives in the context.
        """
        missing = [name for name in names if name not in self._tools]
        if missing:
            raise ValueError(f"cannot build a subset with unknown tools: {missing}")
        return ToolRegistry((self._tools[name] for name in names), ctx or self.ctx)


def file_tools() -> tuple[Tool, ...]:
    """read, write, edit, multi_edit."""
    return (ReadTool(), WriteTool(), EditTool(), MultiEditTool())


def search_tools() -> tuple[Tool, ...]:
    """glob, grep, ls."""
    return (GlobTool(), GrepTool(), LsTool())


def shell_tools(session: ShellSession) -> tuple[Tool, ...]:
    """bash and bash_output, sharing one persistent shell."""
    return (BashTool(session), BashOutputTool(session))


def build_registry(
    ctx: ToolContext,
    *,
    shell: ShellSession | None = None,
    subagent_runner: SubagentRunner | None = None,
    subagent_types: Mapping[str, SubagentType] | None = None,
    fetch: Fetcher | None = None,
    extract: Extractor | None = None,
    search: Searcher | None = None,
    clock: Clock | None = None,
) -> ToolRegistry:
    """Assemble a session's tools.

    Every group is opt-in by supplying its dependency. A session with no ``shell``
    has no ``bash``, and a model told about no ``bash`` does not try to use one — far
    better than a ``bash`` that exists and always errors.
    """
    tools: list[Tool] = [*file_tools(), *search_tools(), TodoWriteTool()]
    if shell is not None:
        tools.extend(shell_tools(shell))
    if subagent_runner is not None:
        tools.append(TaskTool(subagent_runner, types=subagent_types or BUILTIN_SUBAGENTS))
    if fetch is not None and extract is not None:
        if clock is None:
            raise ValueError("web_fetch needs a clock so its cache can expire")
        tools.append(WebFetchTool(fetch=fetch, extract=extract, clock=clock))
    if search is not None:
        tools.append(WebSearchTool(search=search))
    return ToolRegistry(tools, ctx)
