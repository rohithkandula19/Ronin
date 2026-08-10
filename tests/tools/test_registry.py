"""The registry, and the one thing that must be true of all fourteen tools at once.

``test_every_tool_description_is_written_like_a_prompt`` is the gate the work order
actually asks for: half of agent quality is in those strings, so "did anyone write the
when-not-to-use section" is checked mechanically rather than by review.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import FakeNet, RecordingRunner, context, use, write_file

from ronin.core.types import DangerLevel
from ronin.tools import ToolRegistry, build_registry, file_tools, search_tools
from ronin.tools.shell import PersistentShell, ShellSession


def full_registry(root: Path) -> ToolRegistry:
    net = FakeNet({"https://x.test/a": ("text/html", "<p>hi</p>")})
    return build_registry(
        context(root),
        shell=ShellSession(shell=PersistentShell(cwd=root)),
        subagent_runner=RecordingRunner(),
        fetch=net.fetch,
        extract=net.extract,
        search=net.search,
        clock=net.clock,
    )


EXPECTED = {
    "read",
    "write",
    "edit",
    "multi_edit",
    "glob",
    "grep",
    "ls",
    "todo_write",
    "bash",
    "bash_output",
    "task",
    "web_fetch",
    "web_search",
}


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def test_a_full_registry_has_every_tool(tmp_path: Path) -> None:
    names = {spec.name for spec in full_registry(tmp_path).specs()}
    assert names == EXPECTED


def test_a_registry_with_no_dependencies_has_only_the_local_tools(tmp_path: Path) -> None:
    """Not passing a dependency removes the tool, rather than leaving one that errors."""
    names = {spec.name for spec in build_registry(context(tmp_path)).specs()}
    assert names == {"read", "write", "edit", "multi_edit", "glob", "grep", "ls", "todo_write"}
    assert "bash" not in names
    assert "web_fetch" not in names


def test_web_fetch_without_a_clock_is_a_configuration_error(tmp_path: Path) -> None:
    net = FakeNet()
    with pytest.raises(ValueError, match="needs a clock"):
        build_registry(context(tmp_path), fetch=net.fetch, extract=net.extract)


def test_a_duplicate_tool_name_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate tool name"):
        ToolRegistry([*file_tools(), *file_tools()], context(tmp_path))


def test_get_returns_a_spec_for_a_known_tool_and_none_otherwise(tmp_path: Path) -> None:
    registry = build_registry(context(tmp_path))
    assert registry.get("read") is not None
    assert registry.get("hallucinated") is None


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


async def test_execute_runs_the_named_tool(tmp_path: Path) -> None:
    write_file(tmp_path, "a.py", "x = 1\n")
    registry = build_registry(context(tmp_path))
    result = await registry.execute(use("read", path="a.py"))
    assert result.ok
    assert "x = 1" in result.content


async def test_a_hallucinated_tool_name_is_a_value_not_a_raise(tmp_path: Path) -> None:
    registry = build_registry(context(tmp_path))
    result = await registry.execute(use("frobnicate", x=1))
    assert not result.ok
    assert "no tool called 'frobnicate'" in result.error
    assert "read" in result.error, "list what is available so the model can recover"


async def test_the_session_context_is_shared_across_tools(tmp_path: Path) -> None:
    """read marking a file must let write through — they are the same session."""
    write_file(tmp_path, "a.py", "old\n")
    registry = build_registry(context(tmp_path))
    blocked = await registry.execute(use("write", path="a.py", content="new\n"))
    assert not blocked.ok
    await registry.execute(use("read", path="a.py"))
    allowed = await registry.execute(use("write", path="a.py", content="new\n"))
    assert allowed.ok


async def test_a_tool_that_raises_unexpectedly_still_returns_a_value(tmp_path: Path) -> None:
    """The loop's contract: errors are values across this boundary, always."""
    from collections.abc import Mapping
    from typing import Any, ClassVar

    from ronin.core.types import ToolResult
    from ronin.tools import Example, Tool, ToolContext

    class Exploding(Tool):
        name = "explode"
        summary = "Always raises."
        when_to_use = "Never."
        schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
        examples: ClassVar[tuple[Example, ...]] = (Example(call="explode()", result="boom"),)

        async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
            raise RuntimeError("boom")

    registry = ToolRegistry([Exploding()], context(tmp_path))
    result = await registry.execute(use("explode"))
    assert not result.ok
    assert "failed unexpectedly" in result.error
    assert "RuntimeError: boom" in result.error


# --------------------------------------------------------------------------- #
# Subsets — how a subagent gets a narrower toolset
# --------------------------------------------------------------------------- #


def test_a_subset_contains_only_the_named_tools(tmp_path: Path) -> None:
    subset = full_registry(tmp_path).subset(["read", "grep"])
    assert {spec.name for spec in subset.specs()} == {"read", "grep"}


def test_a_subset_shares_the_parent_context(tmp_path: Path) -> None:
    registry = full_registry(tmp_path)
    assert registry.subset(["read"]).ctx is registry.ctx


def test_a_subset_with_an_unknown_tool_is_an_error_not_a_silent_omission(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown tools"):
        full_registry(tmp_path).subset(["read", "teleport"])


def test_every_builtin_subagent_type_names_only_real_tools(tmp_path: Path) -> None:
    """A typo in a subagent's tool list would look like the model being unhelpful."""
    from ronin.tools.task import BUILTIN_SUBAGENTS

    registry = full_registry(tmp_path)
    for subagent in BUILTIN_SUBAGENTS.values():
        registry.subset(list(subagent.tools))


# --------------------------------------------------------------------------- #
# The description gate
# --------------------------------------------------------------------------- #


def test_every_tool_description_is_written_like_a_prompt(tmp_path: Path) -> None:
    """when to use, when NOT to use, and at least one worked example. All fourteen."""
    for spec in full_registry(tmp_path).specs():
        assert "WHEN TO USE" in spec.description, spec.name
        assert "WHEN NOT TO USE" in spec.description, spec.name
        assert "EXAMPLE" in spec.description, spec.name
        assert len(spec.description) > 200, f"{spec.name}: too short to be a prompt"


def test_every_tool_has_an_object_schema(tmp_path: Path) -> None:
    for spec in full_registry(tmp_path).specs():
        assert spec.json_schema.get("type") == "object", spec.name


def test_a_tool_missing_its_when_to_use_is_rejected() -> None:
    from collections.abc import Mapping
    from typing import Any, ClassVar

    from ronin.core.types import ToolResult
    from ronin.tools import Example, Tool, ToolContext

    class Lazy(Tool):
        name = "lazy"
        summary = "Does a thing."
        schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
        examples: ClassVar[tuple[Example, ...]] = (Example(call="lazy()", result="x"),)

        async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
            return ToolResult(ok=True)

    with pytest.raises(ValueError, match="must say when to use it"):
        Lazy().spec()


def test_a_tool_with_no_example_is_rejected() -> None:
    from collections.abc import Mapping
    from typing import Any, ClassVar

    from ronin.core.types import ToolResult
    from ronin.tools import Tool, ToolContext

    class NoExample(Tool):
        name = "noex"
        summary = "Does a thing."
        when_to_use = "Sometimes."
        schema: ClassVar[Mapping[str, Any]] = {"type": "object"}

        async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
            return ToolResult(ok=True)

    with pytest.raises(ValueError, match="worked example"):
        NoExample().spec()


# --------------------------------------------------------------------------- #
# Danger levels
# --------------------------------------------------------------------------- #


def test_only_the_tools_that_change_things_require_approval(tmp_path: Path) -> None:
    gated = {spec.name for spec in full_registry(tmp_path).specs() if spec.requires_approval}
    assert gated == {"write", "edit", "multi_edit", "bash"}


def test_the_read_only_tools_are_declared_read_only(tmp_path: Path) -> None:
    for spec in full_registry(tmp_path).specs():
        if spec.name in {"read", "glob", "grep", "ls", "todo_write", "web_search", "bash_output"}:
            assert spec.danger_level is DangerLevel.READ_ONLY, spec.name


def test_bash_is_the_most_dangerous_tool(tmp_path: Path) -> None:
    specs = {spec.name: spec for spec in full_registry(tmp_path).specs()}
    assert specs["bash"].danger_level is DangerLevel.DESTRUCTIVE


def test_the_registry_satisfies_the_loops_protocol(tmp_path: Path) -> None:
    from ronin.core.protocols import ToolRegistry as Protocol

    assert isinstance(build_registry(context(tmp_path)), Protocol)


def test_search_tools_are_a_stable_group() -> None:
    assert [tool.name for tool in search_tools()] == ["glob", "grep", "ls"]
