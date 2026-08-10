"""Remote MCP tools, presented as ordinary :class:`ronin.tools.base.Tool` objects.

Everything above this module — the registry, the loop, the approval gate, the UI —
must be unable to tell an MCP tool from a built-in one. That is the whole design
goal, and it has three consequences:

* **One naming function.** ``mcp__<server>__<tool>``, built in exactly one place
  (:func:`namespaced_name`) and validated there. Two servers cannot collide,
  because a server name may not contain the ``__`` separator (enforced in
  :mod:`ronin.mcp.config`) and server names are the keys of a JSON object, so
  they are unique. Collision is prevented by construction, not by a check that
  someone has to remember to run.
* **Descriptions are rewritten into the house format.** An MCP server supplies a
  ``description`` and nothing else. A description with no "when *not* to use"
  measurably degrades tool selection — it is the half that stops a tool being
  used as a hammer — so one is **synthesized** here, and it says so in the text.
  A synthesized line that admits it is synthesized is honest; a missing one is a
  silent quality regression.
* **Danger only ever goes up.** A server's ``annotations`` may raise a tool's
  danger level above what the config declared. They may never lower it. A remote
  server asserting ``readOnlyHint: true`` is a claim by the thing we are trying to
  contain, and believing it would make the gate decorative.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..core.types import DangerLevel, ToolResult, ToolSpec
from ..tools.base import Example, Tool, ToolContext
from ..tools.registry import ToolRegistry
from .config import McpServerConfig
from .jsonrpc import McpError

#: The fixed prefix, and the separator between the three segments.
NAMESPACE_PREFIX = "mcp"
NAME_SEPARATOR = "__"

#: Providers cap a tool name at 64 characters and accept only these characters.
#: A name past the cap is rejected by the API *after* the session is assembled,
#: which surfaces as a mid-conversation 400 rather than a startup error — so the
#: check happens here, at the point the name is minted.
MAX_TOOL_NAME_CHARS = 64
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")

#: Placeholders for a synthesized example, by JSON Schema type.
_PLACEHOLDERS: Mapping[str, Any] = {
    "string": "…",
    "integer": 0,
    "number": 0,
    "boolean": False,
    "array": [],
    "object": {},
    "null": None,
}

#: The synthesized "when not to use" line. Marked as synthesized on purpose: a
#: reader of the assembled prompt must be able to tell what the upstream server
#: actually said from what Ronin filled in.
_SYNTHESIZED_MARKER = (
    "(synthesized by Ronin — the MCP server supplied no guidance on when this tool is a poor fit)"
)


class NamingError(McpError):
    """A remote tool cannot be given a legal namespaced name."""


#: How a :class:`RemoteTool` reaches its server. Injected so a remote tool has no
#: reference to a client, a transport, or a socket — which is what lets the tests
#: exercise the wrapper with a two-line stub.
RemoteInvoke = Callable[[str, Mapping[str, Any]], Awaitable[ToolResult]]


def namespaced_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>``, or raise :class:`NamingError`.

    The single point at which an MCP tool gets its local name. Rejects a segment
    with characters a provider will not accept, and a total length past the cap,
    naming both the server and the tool so the operator knows which entry to fix.
    """
    if not server or not tool:
        raise NamingError(f"both a server and a tool name are required, got {server!r}/{tool!r}")
    if NAME_SEPARATOR in server:
        raise NamingError(
            f"server {server!r} contains {NAME_SEPARATOR!r}, which is the "
            "namespace separator; the name would be ambiguous"
        )
    for label, segment in (("server", server), ("tool", tool)):
        if not _SEGMENT_RE.match(segment):
            raise NamingError(
                f"{label} name {segment!r} (server {server!r}, tool {tool!r}) has "
                "characters outside [A-Za-z0-9_-], which providers reject in a "
                "tool name"
            )
    name = f"{NAMESPACE_PREFIX}{NAME_SEPARATOR}{server}{NAME_SEPARATOR}{tool}"
    if len(name) > MAX_TOOL_NAME_CHARS:
        raise NamingError(
            f"{name!r} is {len(name)} characters, over the {MAX_TOOL_NAME_CHARS}-"
            f"character limit (server {server!r}, tool {tool!r}); shorten the "
            "server name in .ronin/mcp.json"
        )
    return name


def split_namespaced_name(name: str) -> tuple[str, str] | None:
    """``(server, tool)`` for a namespaced name, else ``None``.

    Unambiguous because a server name cannot contain the separator, so the split
    is "prefix, server, everything else" and a remote tool called ``a__b`` still
    round-trips.
    """
    parts = name.split(NAME_SEPARATOR, 2)
    if len(parts) != 3 or parts[0] != NAMESPACE_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def is_namespaced(name: str) -> bool:
    return split_namespaced_name(name) is not None


# --------------------------------------------------------------------------- #
# Descriptions
# --------------------------------------------------------------------------- #


def render_description(
    summary: str,
    when_to_use: str,
    when_not_to_use: str,
    examples: Sequence[Example],
) -> str:
    """The house description format, as a free function.

    Mirrors ``ronin.tools.base.Tool.describe`` byte for byte. It is duplicated
    rather than reused because ``Tool.schema``/``Tool.examples`` are ``ClassVar``s
    — per-class, and a remote tool's schema is per-*instance* — so a
    :class:`RemoteTool` cannot populate them and cannot use the inherited
    renderer. The right fix is for ``ronin.tools.base`` to expose this function;
    that file belongs to another owner, so the duplication is declared here
    instead of hidden.
    """
    parts = [summary.strip()]
    if when_to_use:
        parts.append(f"WHEN TO USE\n{when_to_use.strip()}")
    if when_not_to_use:
        parts.append(f"WHEN NOT TO USE\n{when_not_to_use.strip()}")
    for example in examples:
        parts.append(f"EXAMPLE\n{example.call.strip()}\n→ {example.result.strip()}")
    return "\n\n".join(parts)


@dataclass(frozen=True, slots=True)
class Described:
    """The four pieces of a house-format description, before rendering."""

    summary: str
    when_to_use: str
    when_not_to_use: str
    examples: tuple[Example, ...]


def adapt_description(server: str, local_name: str, descriptor: Mapping[str, Any]) -> Described:
    """Turn an MCP tool descriptor into the house format.

    The upstream ``description`` becomes the summary and the body of "when to
    use"; "when not to use" and the worked example do not exist upstream and are
    synthesized. Both synthesized parts are labelled in the text.
    """
    upstream = str(descriptor.get("description") or "").strip()
    head, _, tail = upstream.partition("\n")
    summary = head.strip() or (
        f"{descriptor.get('name', local_name)!r} on the {server!r} MCP server "
        "(the server supplied no description)."
    )
    when_to_use = "\n".join(
        part
        for part in (
            tail.strip(),
            f"This tool is provided by the {server!r} MCP server. Reach for it "
            f"when the task needs {server} specifically and no local tool covers "
            "it; every call is a round trip to another process.",
        )
        if part
    )
    when_not_to_use = (
        f"{_SYNTHESIZED_MARKER} Do not use it for work a local tool already does "
        "— read, grep, glob and bash act on this repo directly and are faster and "
        f"cheaper. Do not use it to guess at data you could read. If the {server!r} "
        "server is unreachable this tool fails with a message saying so; treat "
        "that as final for the rest of the session rather than retrying in a loop."
    )
    return Described(
        summary=summary,
        when_to_use=when_to_use,
        when_not_to_use=when_not_to_use,
        examples=(synthesize_example(local_name, descriptor),),
    )


def synthesize_example(local_name: str, descriptor: Mapping[str, Any]) -> Example:
    """A minimal worked call, built from the schema's required properties.

    Models copy examples far more reliably than they read schemas, which is why
    ``Tool.spec`` insists on one. An MCP server supplies none, so the arguments
    are derived from ``inputSchema``: a declared ``default``, else the first
    ``enum`` member, else a placeholder for the declared type.
    """
    schema = descriptor.get("inputSchema")
    properties: Mapping[str, Any] = {}
    required: Sequence[Any] = ()
    if isinstance(schema, Mapping):
        raw_properties = schema.get("properties")
        if isinstance(raw_properties, Mapping):
            properties = raw_properties
        raw_required = schema.get("required")
        if isinstance(raw_required, (list, tuple)):
            required = raw_required
    names = [str(name) for name in required if str(name) in properties] or list(properties)[:1]
    args = {name: _placeholder(properties.get(name)) for name in names}
    rendered = ", ".join(f"{key}={value!r}" for key, value in args.items())
    return Example(
        call=f"{local_name}({rendered})",
        result=(
            "the server's reply as text (synthesized example — argument values "
            "come from the tool's own schema, not from a recorded call)"
        ),
    )


def _placeholder(spec: object) -> Any:
    if not isinstance(spec, Mapping):
        return _PLACEHOLDERS["string"]
    if "default" in spec:
        return spec["default"]
    enum = spec.get("enum")
    if isinstance(enum, (list, tuple)) and enum:
        return enum[0]
    declared = spec.get("type")
    if isinstance(declared, (list, tuple)) and declared:
        declared = declared[0]
    if isinstance(declared, str):
        return _PLACEHOLDERS.get(declared, _PLACEHOLDERS["string"])
    return _PLACEHOLDERS["string"]


# --------------------------------------------------------------------------- #
# Specs
# --------------------------------------------------------------------------- #


def danger_for(config: McpServerConfig, descriptor: Mapping[str, Any]) -> tuple[DangerLevel, bool]:
    """``(danger_level, requires_approval)`` for one remote tool.

    Starts from the config — which fails closed for a server that declared
    nothing — and lets the server's own ``annotations`` *raise* it. Never lower
    it: ``readOnlyHint: true`` from a remote server is an assertion by the thing
    the gate exists to contain, so it is read and ignored for gating purposes.
    """
    level = config.effective_danger_level
    approval = config.effective_requires_approval
    annotations = descriptor.get("annotations")
    if isinstance(annotations, Mapping) and annotations.get("destructiveHint") is True:
        if level < DangerLevel.DESTRUCTIVE:
            level = DangerLevel.DESTRUCTIVE
        approval = True
    if level >= DangerLevel.DESTRUCTIVE:
        approval = True
    return level, approval


def spec_for(config: McpServerConfig, descriptor: Mapping[str, Any]) -> ToolSpec:
    """Map an MCP tool descriptor onto a :class:`ToolSpec`.

    Raises :class:`NamingError` if the name cannot be namespaced legally; the
    caller drops that one tool rather than losing the whole server.
    """
    remote_name = descriptor.get("name")
    if not isinstance(remote_name, str) or not remote_name:
        raise NamingError(
            f"server {config.name!r} listed a tool with no usable name: {descriptor!r}"
        )
    local_name = namespaced_name(config.name, remote_name)
    described = adapt_description(config.name, local_name, descriptor)
    level, approval = danger_for(config, descriptor)
    schema = descriptor.get("inputSchema")
    return ToolSpec(
        name=local_name,
        description=render_description(
            described.summary,
            described.when_to_use,
            described.when_not_to_use,
            described.examples,
        ),
        json_schema=dict(schema) if isinstance(schema, Mapping) else _EMPTY_SCHEMA,
        danger_level=level,
        requires_approval=approval,
    )


#: What a tool that declared no schema gets. An empty object schema is honest
#: ("no arguments described") where an absent one makes some providers reject the
#: whole tool list.
_EMPTY_SCHEMA: Mapping[str, Any] = {"type": "object", "properties": {}}


# --------------------------------------------------------------------------- #
# The wrapper
# --------------------------------------------------------------------------- #


class RemoteTool(Tool):
    """One MCP server's tool, wearing the local :class:`Tool` interface.

    Overrides ``spec`` and ``describe`` rather than setting the base class
    attributes because ``Tool.schema`` and ``Tool.examples`` are ``ClassVar``s and
    a remote tool's schema is known per instance, not per class. Everything the
    registry and the loop touch — ``name``, ``spec()``, ``execute()`` — behaves
    identically to a built-in tool, which is the point.
    """

    def __init__(
        self,
        *,
        server: str,
        remote_name: str,
        spec: ToolSpec,
        invoke: RemoteInvoke,
    ) -> None:
        self.server = server
        self.remote_name = remote_name
        self.name = spec.name
        self.summary = spec.description.partition("\n")[0]
        self.danger_level = spec.danger_level
        self.requires_approval = spec.requires_approval
        self._spec = spec
        self._invoke = invoke

    def describe(self) -> str:
        return self._spec.description

    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        """Forward to the server. ``ctx`` is unused: the work happens elsewhere.

        A remote tool cannot honour the local root, the read-before-write guard, or
        the injected environment — the server has its own filesystem view. That is
        stated here rather than pretended away, and it is why an undeclared server
        is gated by default.
        """
        return await self._invoke(self.remote_name, args)


def remote_tools(
    config: McpServerConfig,
    descriptors: Sequence[object],
    invoke: RemoteInvoke,
) -> tuple[tuple[RemoteTool, ...], tuple[str, ...]]:
    """``(tools, skipped)`` for one server's ``tools/list`` reply.

    A descriptor that cannot be named legally is skipped with a reason rather than
    failing the server: one over-long tool name must not cost a session the other
    nineteen tools on that server.

    ``descriptors`` is typed ``Sequence[object]`` on purpose: these entries came
    off a wire a remote process controls, so "each one is a mapping" is a runtime
    fact to check rather than a static one to assume.
    """
    tools: list[RemoteTool] = []
    skipped: list[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping):
            skipped.append(
                f"{config.name}: tools/list returned a non-object entry "
                f"({type(descriptor).__name__})"
            )
            continue
        try:
            spec = spec_for(config, descriptor)
        except (NamingError, ValueError) as exc:
            skipped.append(f"{config.name}: {exc}")
            continue
        tools.append(
            RemoteTool(
                server=config.name,
                remote_name=str(descriptor["name"]),
                spec=spec,
                invoke=invoke,
            )
        )
    return tuple(tools), tuple(skipped)


def extend_registry(registry: ToolRegistry, extra: Sequence[Tool]) -> ToolRegistry:
    """A new registry with ``extra`` appended, sharing the original's context.

    How the orchestrator folds MCP tools into a session. ``ToolRegistry`` raises on
    a duplicate name, so an accidental double-registration is a startup failure
    rather than a tool that silently shadows another.
    """
    return ToolRegistry((*registry.tools(), *extra), registry.ctx)


__all__ = [
    "MAX_TOOL_NAME_CHARS",
    "NAMESPACE_PREFIX",
    "NAME_SEPARATOR",
    "Described",
    "NamingError",
    "RemoteInvoke",
    "RemoteTool",
    "adapt_description",
    "danger_for",
    "extend_registry",
    "is_namespaced",
    "namespaced_name",
    "remote_tools",
    "render_description",
    "spec_for",
    "split_namespaced_name",
    "synthesize_example",
]
