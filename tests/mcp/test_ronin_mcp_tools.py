"""Namespacing, description adaptation, and the danger rules for a remote tool.

Two behaviours here are load-bearing and easy to lose in a refactor: a collision
between two servers cannot be *expressed*, and a server's own annotations can raise
its danger level but never lower it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from mcp_harness import server_config, tool_descriptor

from ronin.core.types import DangerLevel, ToolResult
from ronin.mcp.tools import (
    MAX_TOOL_NAME_CHARS,
    NamingError,
    RemoteTool,
    adapt_description,
    danger_for,
    extend_registry,
    is_namespaced,
    namespaced_name,
    remote_tools,
    spec_for,
    split_namespaced_name,
    synthesize_example,
)
from ronin.tools.base import ToolContext
from ronin.tools.registry import build_registry


async def _invoke(name: str, arguments: Mapping[str, Any]) -> ToolResult:
    return ToolResult(ok=True, content=f"{name}:{sorted(arguments.items())}")


# --------------------------------------------------------------------------- #
# Naming
# --------------------------------------------------------------------------- #


def test_a_name_is_prefixed_server_and_tool() -> None:
    assert namespaced_name("docs", "search") == "mcp__docs__search"


def test_two_servers_with_the_same_tool_get_different_names() -> None:
    assert namespaced_name("docs", "search") != namespaced_name("tickets", "search")


def test_a_namespaced_name_round_trips() -> None:
    assert split_namespaced_name(namespaced_name("docs", "search")) == ("docs", "search")


def test_a_tool_name_containing_the_separator_still_round_trips() -> None:
    """Server names cannot contain `__`, so the split is unambiguous from the left."""
    name = namespaced_name("docs", "search__v2")
    assert split_namespaced_name(name) == ("docs", "search__v2")


@pytest.mark.parametrize(
    "name", ["read", "mcp__docs", "mcp____search", "other__docs__search", "mcp__docs__"]
)
def test_a_name_that_is_not_ours_is_reported_as_such(name: str) -> None:
    assert split_namespaced_name(name) is None
    assert not is_namespaced(name)


def test_a_server_name_with_the_separator_cannot_be_namespaced() -> None:
    with pytest.raises(NamingError, match="separator"):
        namespaced_name("a__b", "t")


@pytest.mark.parametrize(
    ("server", "tool"),
    [("docs.v2", "search"), ("docs", "search.all"), ("docs", "search tool"), ("dö", "s")],
)
def test_characters_a_provider_rejects_are_refused_here(server: str, tool: str) -> None:
    """A name the API rejects surfaces as a mid-conversation 400, not a startup error."""
    with pytest.raises(NamingError, match="A-Za-z0-9_-"):
        namespaced_name(server, tool)


def test_an_over_long_name_is_refused_with_the_fix_named() -> None:
    with pytest.raises(NamingError) as caught:
        namespaced_name("s" * 40, "t" * 40)
    assert str(MAX_TOOL_NAME_CHARS) in str(caught.value)
    assert "shorten the server name" in str(caught.value)


def test_a_name_exactly_at_the_limit_is_allowed() -> None:
    name = namespaced_name("s" * 20, "t" * (MAX_TOOL_NAME_CHARS - 20 - len("mcp____")))
    assert len(name) == MAX_TOOL_NAME_CHARS


# --------------------------------------------------------------------------- #
# Descriptions
# --------------------------------------------------------------------------- #


def test_the_first_line_of_the_upstream_description_becomes_the_summary() -> None:
    described = adapt_description(
        "docs", "mcp__docs__search", tool_descriptor("search", description="Find docs.\nMore.")
    )
    assert described.summary == "Find docs."
    assert "More." in described.when_to_use


def test_an_empty_upstream_description_still_produces_a_summary() -> None:
    described = adapt_description("docs", "mcp__docs__search", {"name": "search"})
    assert "supplied no description" in described.summary


def test_the_when_not_to_use_half_is_synthesized_and_says_so() -> None:
    """A raw upstream description has no 'when not to use'; a missing one degrades
    tool selection, and a silently invented one is worse than a labelled one."""
    described = adapt_description("docs", "mcp__docs__search", tool_descriptor("search"))
    assert "synthesized by Ronin" in described.when_not_to_use
    assert "read, grep, glob and bash" in described.when_not_to_use


def test_the_rendered_description_uses_the_house_section_order() -> None:
    spec = spec_for(server_config("docs"), tool_descriptor("search"))
    body = spec.description
    assert body.index("WHEN TO USE") < body.index("WHEN NOT TO USE") < body.index("EXAMPLE")


def test_the_example_is_built_from_the_schema_and_is_labelled_synthetic() -> None:
    example = synthesize_example(
        "mcp__docs__q",
        tool_descriptor(
            "q",
            schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 5},
                    "mode": {"enum": ["fast", "slow"]},
                    "flag": {"type": "boolean"},
                },
                "required": ["limit", "mode", "flag"],
            },
        ),
    )
    assert "limit=5" in example.call
    assert "mode='fast'" in example.call
    assert "flag=False" in example.call
    assert "synthesized example" in example.result


def test_a_tool_with_no_schema_still_gets_an_example_and_an_object_schema() -> None:
    spec = spec_for(server_config("docs"), {"name": "ping", "description": "Ping."})
    assert spec.json_schema == {"type": "object", "properties": {}}
    assert "EXAMPLE" in spec.description


# --------------------------------------------------------------------------- #
# Danger
# --------------------------------------------------------------------------- #


def test_an_undeclared_server_makes_its_tools_gated() -> None:
    level, approval = danger_for(server_config("x"), tool_descriptor("t"))
    assert level is DangerLevel.MUTATING
    assert approval is True


def test_a_read_only_hint_from_the_server_does_not_lower_the_gate() -> None:
    """The hint is an assertion by the thing the gate exists to contain."""
    level, approval = danger_for(
        server_config("x"), tool_descriptor("t", annotations={"readOnlyHint": True})
    )
    assert level is DangerLevel.MUTATING
    assert approval is True


def test_a_destructive_hint_from_the_server_does_raise_it() -> None:
    config = server_config("x", danger_level=DangerLevel.READ_ONLY, requires_approval=False)
    level, approval = danger_for(
        config, tool_descriptor("t", annotations={"destructiveHint": True})
    )
    assert level is DangerLevel.DESTRUCTIVE
    assert approval is True


def test_a_waived_server_produces_an_ungated_spec() -> None:
    config = server_config("docs", danger_level=DangerLevel.READ_ONLY, requires_approval=False)
    spec = spec_for(config, tool_descriptor("search"))
    assert spec.danger_level is DangerLevel.READ_ONLY
    assert spec.requires_approval is False


# --------------------------------------------------------------------------- #
# The wrapper
# --------------------------------------------------------------------------- #


async def test_a_remote_tool_presents_exactly_like_a_local_one(tmp_path: Path) -> None:
    config = server_config("docs", danger_level=DangerLevel.READ_ONLY, requires_approval=False)
    (tools, skipped) = remote_tools(config, [tool_descriptor("search")], _invoke)
    assert skipped == ()
    (tool,) = tools
    assert tool.name == "mcp__docs__search"
    assert tool.spec().name == "mcp__docs__search"
    assert tool.describe() == tool.spec().description
    result = await tool.execute({"query": "x"}, ToolContext(root=tmp_path))
    assert result.ok
    assert result.content == "search:[('query', 'x')]"


async def test_the_wrapper_turns_an_unexpected_exception_into_a_value(tmp_path: Path) -> None:
    """`Tool.execute`'s error boundary must hold for remote tools too."""

    async def boom(name: str, arguments: Mapping[str, Any]) -> ToolResult:
        raise RuntimeError("the socket melted")

    tool = RemoteTool(
        server="docs",
        remote_name="search",
        spec=spec_for(server_config("docs"), tool_descriptor("search")),
        invoke=boom,
    )
    result = await tool.execute({}, ToolContext(root=tmp_path))
    assert not result.ok
    assert "the socket melted" in result.error


def test_a_tool_that_cannot_be_named_is_skipped_not_fatal() -> None:
    """One bad name must not cost the session the other nineteen tools."""
    config = server_config("docs")
    tools, skipped = remote_tools(
        config,
        [tool_descriptor("ok"), tool_descriptor("not.legal"), tool_descriptor("also_ok")],
        _invoke,
    )
    assert [tool.remote_name for tool in tools] == ["ok", "also_ok"]
    assert len(skipped) == 1
    assert "not.legal" in skipped[0]
    assert "docs" in skipped[0]


def test_a_non_object_descriptor_is_skipped_with_a_reason() -> None:
    tools, skipped = remote_tools(server_config("docs"), ["nonsense", 7], _invoke)
    assert tools == ()
    assert len(skipped) == 2
    assert "non-object entry" in skipped[0]


def test_a_descriptor_with_no_name_is_skipped() -> None:
    tools, skipped = remote_tools(server_config("docs"), [{"description": "x"}], _invoke)
    assert tools == ()
    assert "no usable name" in skipped[0]


# --------------------------------------------------------------------------- #
# Registry integration
# --------------------------------------------------------------------------- #


def test_remote_tools_drop_into_a_real_registry(tmp_path: Path) -> None:
    ctx = ToolContext(root=tmp_path, env={})
    config = server_config("docs", danger_level=DangerLevel.READ_ONLY, requires_approval=False)
    tools, _ = remote_tools(config, [tool_descriptor("search")], _invoke)
    registry = extend_registry(build_registry(ctx), tools)
    assert registry.get("mcp__docs__search") is not None
    assert registry.get("read") is not None
    assert registry.ctx is ctx


def test_two_servers_tools_coexist_in_one_registry(tmp_path: Path) -> None:
    """The collision case, end to end: same tool name, two servers, one registry."""
    ctx = ToolContext(root=tmp_path, env={})
    first, _ = remote_tools(server_config("docs"), [tool_descriptor("search")], _invoke)
    second, _ = remote_tools(server_config("tickets"), [tool_descriptor("search")], _invoke)
    registry = extend_registry(build_registry(ctx), [*first, *second])
    names = {spec.name for spec in registry.specs()}
    assert {"mcp__docs__search", "mcp__tickets__search"} <= names


def test_registering_the_same_remote_tool_twice_is_a_startup_failure(tmp_path: Path) -> None:
    """Better than a tool that silently shadows another."""
    ctx = ToolContext(root=tmp_path, env={})
    tools, _ = remote_tools(server_config("docs"), [tool_descriptor("search")], _invoke)
    with pytest.raises(ValueError, match="duplicate tool name"):
        extend_registry(build_registry(ctx), [*tools, *tools])
