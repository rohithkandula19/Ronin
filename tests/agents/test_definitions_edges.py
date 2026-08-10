"""The frontmatter parser's refusals, and the path lane's last two branches.

`test_definitions.py` covers the happy path and the main refusals thoroughly. What
was left were thirteen lines split between two kinds of thing:

* **Validator branches on `AgentDefinition`.** A subagent definition is a *grant* —
  it says which tools something may use and which paths it may write. A malformed
  grant that is accepted quietly is the worst outcome available here, so every
  refusal is worth a test even when reaching it needs direct construction.
* **Two accessors and a defence-in-depth fallback in `RestrictedPaths`.**

Where a branch is reachable through a real `.md` file, these tests go through the
file, because that is the path a user's config actually takes. Where it is only
reachable by constructing the dataclass, they say so.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from agents_harness import context, registry, use

from ronin.agents.definitions import (
    AgentDefinition,
    FrontmatterError,
    RestrictedPaths,
    _inline_list,
    parse_definition,
    restrict_writes,
)
from ronin.tools.base import ToolContext
from ronin.tools.files import WriteTool


def document(frontmatter: str, body: str = "You do the thing.") -> str:
    return f"---\n{frontmatter}\n---\n\n{body}\n"


# --------------------------------------------------------------------------- #
# The frontmatter lexer
# --------------------------------------------------------------------------- #


def test_an_empty_inline_list_is_empty_rather_than_a_list_of_nothing() -> None:
    """`[]` is a legitimate way to write "none", distinct from a malformed entry."""
    assert _inline_list("[]", "x.md", 1) == ()
    assert _inline_list("[ ]", "x.md", 1) == ()


def test_blank_lines_before_the_opening_fence_are_skipped() -> None:
    """A file that starts with a newline is a normal thing for an editor to produce,
    and refusing it would be a puzzle for the person who wrote the agent."""
    text = "\n\n" + document("name: doc\ndescription: docs\ntools: [read]")
    definition = parse_definition(text, source="doc.md")
    assert definition.name == "doc"


def test_a_file_of_only_blank_lines_is_refused_with_a_line_number() -> None:
    with pytest.raises(FrontmatterError, match=re.escape("doc.md")):
        parse_definition("\n\n\n", source="doc.md")


# --------------------------------------------------------------------------- #
# Scalars that arrive as lists
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("frontmatter", "needle"),
    [
        ("name: [a, b]\ndescription: d\ntools: [read]", "must be scalars"),
        ("name: n\ndescription: [a, b]\ntools: [read]", "must be scalars"),
        ("name: n\ndescription: d\ntools: [read]\nmax_iterations: [1, 2]", "not a list"),
        ("name: n\ndescription: d\ntools: [read]\nmodel: [fast, main]", "single role name"),
    ],
    ids=["name", "description", "max_iterations", "model"],
)
def test_a_scalar_key_written_as_a_list_is_refused_by_name(frontmatter: str, needle: str) -> None:
    """`tools` is a list and the others are not, which is exactly the confusion a
    person makes once. The error names the offending key rather than saying the
    frontmatter is bad, because "which line do I fix" is the only question."""
    with pytest.raises(FrontmatterError, match=needle):
        parse_definition(document(frontmatter), source="a.md")


# --------------------------------------------------------------------------- #
# AgentDefinition's own validators
# --------------------------------------------------------------------------- #


def test_a_whitespace_only_description_is_refused_through_a_real_file() -> None:
    """Reachable through the parser: a quoted `"   "` is present, so it passes the
    missing-keys check and is caught by the dataclass instead.

    The description is the only thing the dispatcher reads to decide when to invoke
    an agent, so a blank one produces an agent nothing will ever choose.
    """
    text = document('name: n\ndescription: "   "\ntools: [read]')
    with pytest.raises(FrontmatterError, match="description"):
        parse_definition(text, source="a.md")


def test_zero_iterations_is_refused_through_a_real_file() -> None:
    """`0` is a digit string, so it passes the parser's integer check and reaches the
    validator. An agent that may run zero turns can only ever return nothing."""
    text = document("name: n\ndescription: d\ntools: [read]\nmax_iterations: 0")
    with pytest.raises(FrontmatterError, match="max_iterations"):
        parse_definition(text, source="a.md")


def test_an_agent_with_no_tools_is_refused() -> None:
    """Only reachable by constructing the dataclass: `tools: []` parses to an empty
    tuple, which the parser rejects earlier as a missing required key. Tested here
    because the validator is the floor under every other construction path —
    including anything that builds a definition programmatically."""
    with pytest.raises(ValueError, match="no tools"):
        AgentDefinition(name="n", description="d", tools=(), system_prompt="p")


def test_a_negative_iteration_budget_is_refused() -> None:
    with pytest.raises(ValueError, match="max_iterations >= 1"):
        AgentDefinition(
            name="n", description="d", tools=("read",), system_prompt="p", max_iterations=-1
        )


def test_a_valid_definition_still_constructs() -> None:
    """The control. A validator that refused everything would pass every test above."""
    definition = AgentDefinition(
        name="ok", description="d", tools=("read",), system_prompt="p", max_iterations=1
    )
    assert definition.to_subagent_type().name == "ok"


# --------------------------------------------------------------------------- #
# RestrictedPaths
# --------------------------------------------------------------------------- #


def test_the_wrapper_exposes_what_it_wraps_and_what_it_allows(tmp_path: Path) -> None:
    """Both accessors exist so wiring can introspect a lane without unwrapping it by
    hand — `gated_tools` and the session policy both need to know."""
    inner = WriteTool()
    wrapper = RestrictedPaths(inner, ("tests/**",))
    assert wrapper.inner is inner
    assert wrapper.allowed == ("tests/**",)


async def test_a_list_of_paths_is_checked_entry_by_entry(tmp_path: Path) -> None:
    """A tool taking `paths: [...]` must not be checkable by passing one allowed
    entry alongside a forbidden one — the wrapper reads every string in the list."""
    lane = restrict_writes(registry(tmp_path), ("tests/**",))
    tool = next(t for t in lane.tools() if t.name == "write")
    assert isinstance(tool, RestrictedPaths)

    args: dict[str, Any] = {"paths": ["tests/ok.py", "src/nope.py"], "content": "x"}
    result = await tool.execute(args, context(tmp_path))
    assert not result.ok
    assert "src/nope.py" in (result.error or "")


async def test_a_non_string_entry_in_a_path_list_is_left_to_the_real_tool(
    tmp_path: Path,
) -> None:
    """The wrapper only judges paths it can recognise. A `None` in the list is a
    malformed argument, and the real tool's error message says so far better than a
    path-restriction message would."""
    lane = restrict_writes(registry(tmp_path), ("tests/**",))
    tool = next(t for t in lane.tools() if t.name == "write")
    args: dict[str, Any] = {"paths": [None, 42], "content": "x"}
    result = await tool.execute(args, context(tmp_path))
    assert not result.ok  # refused by `write` itself, not by the lane


async def test_the_restriction_holds_even_if_path_confinement_stopped_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth, and the reason the fallback branch exists.

    `ToolContext.resolve` already refuses anything outside the tree after symlink
    resolution, so in normal operation this wrapper never sees an outside path and
    `relative_to` cannot raise. That makes the fallback unreachable through the
    public path — and *that* is what makes it worth a test: it proves the path lane
    does not silently depend on `resolve` for its own guarantee. If confinement ever
    regressed, a `test-writer` still could not write to `/etc`.
    """
    outside = tmp_path.parent / "outside_the_tree.py"
    monkeypatch.setattr(ToolContext, "resolve", lambda _self, _path: outside)

    lane = restrict_writes(registry(tmp_path), ("tests/**",))
    result = await lane.execute(use("write", path="tests/looks_fine.py", content="x"))

    assert not result.ok
    assert not outside.exists(), "the wrapper let a write escape the tree"
    assert str(outside) in (result.error or "") or "outside" in (result.error or "").lower()
