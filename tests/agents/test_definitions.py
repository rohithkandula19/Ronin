"""subagent definitions: the frontmatter parser, the path lane, and dispatch."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agents_harness import context, registry, seed, use

from ronin.agents.definitions import (
    BUILTIN_AGENTS,
    MODEL_ROLES,
    AgentDefinition,
    FrontmatterError,
    RestrictedPaths,
    build_subagent_registry,
    gated_tools,
    load_agents,
    load_definition,
    load_directory,
    model_roles,
    parse_definition,
    parse_frontmatter,
    path_allowed,
    rank,
    restrict_writes,
    select,
    subagent_catalogue,
)
from ronin.core.types import DangerLevel
from ronin.tools.files import WriteTool
from ronin.tools.task import BUILTIN_SUBAGENTS

GOOD = """\
---
name: doc-writer
# a comment, ignored
description: "Updates documentation under docs/ when the change is prose."
tools: [read, glob, write]
model: fast
write_paths:
  - docs/**
  - README.md
max_iterations: 6
---
You are the doc-writer subagent.

Match the voice of the files already there.
"""


# --------------------------------------------------------------------------- #
# The parser
# --------------------------------------------------------------------------- #


def test_frontmatter_parses_scalars_quoted_strings_and_both_list_forms() -> None:
    fields, body = parse_frontmatter(GOOD, source="doc-writer.md")
    assert fields["name"] == "doc-writer"
    assert fields["description"] == "Updates documentation under docs/ when the change is prose."
    assert fields["tools"] == ("read", "glob", "write")
    assert fields["write_paths"] == ("docs/**", "README.md")
    assert fields["max_iterations"] == "6"
    assert body.startswith("You are the doc-writer subagent.")


def test_the_body_below_the_frontmatter_is_the_system_prompt_verbatim() -> None:
    definition = parse_definition(GOOD, source="doc-writer.md")
    assert definition.system_prompt == (
        "You are the doc-writer subagent.\n\nMatch the voice of the files already there.\n"
    )


def test_a_comma_separated_scalar_is_accepted_as_a_list() -> None:
    text = "---\nname: a\ndescription: d\ntools: read, grep\n---\nbody\n"
    assert parse_definition(text).tools == ("read", "grep")


@pytest.mark.parametrize(
    ("text", "line", "needle"),
    [
        ("name: a\n", 1, "frontmatter fence"),
        ("---\nname: a\ndescription: d\ntools: [read]\n", 5, "never closed"),
        ("---\nname: a\ntool: [read]\n---\nb\n", 3, "unknown key 'tool'"),
        ("---\nname: a\nname: b\n---\nb\n", 3, "duplicate key 'name'"),
        ("---\nname: a\ndescription\n---\nb\n", 3, "expected 'key: value'"),
        ('---\nname: a\ndescription: "oops\n---\nb\n', 3, "unterminated quote"),
        ("---\nname: a\ndescription: >\n---\nb\n", 3, "block scalars"),
        ("---\nname: a\ntools: [read, grep\n---\nb\n", 3, "unclosed inline list"),
        ("---\nname: a\ntools: [read,, grep]\n---\nb\n", 3, "empty entry"),
        ("---\n- orphan\n---\nb\n", 2, "does not follow a 'key:' line"),
    ],
)
def test_malformed_frontmatter_names_the_file_and_the_line(
    text: str, line: int, needle: str
) -> None:
    """A typo must never become a silently missing subagent."""
    with pytest.raises(FrontmatterError) as caught:
        parse_definition(text, source="broken.md")
    assert caught.value.source == "broken.md"
    assert caught.value.line == line
    assert needle in str(caught.value)
    assert str(caught.value).startswith(f"broken.md:{line}:")


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("---\nname: a\ndescription: d\n---\nbody\n", "missing required key(s): ['tools']"),
        ("---\nname: a\ntools: [read]\n---\nbody\n", "['description']"),
        ("---\ndescription: d\ntools: [read]\n---\nbody\n", "['name']"),
        ("---\nname: a\ndescription: d\ntools:\n---\nbody\n", "['tools']"),
        ("---\nname: A Name\ndescription: d\ntools: [read]\n---\nb\n", "must be lowercase"),
        ("---\nname: a\ndescription: d\ntools: [read]\n---\n", "empty system prompt"),
        ("---\nname: a\ndescription: d\ntools: [read]\nmodel: gpt-9\n---\nb\n", "must be one of"),
        (
            "---\nname: a\ndescription: d\ntools: [read]\nmax_iterations: soon\n---\nb\n",
            "positive integer",
        ),
    ],
)
def test_an_incomplete_or_invalid_definition_is_refused_with_a_reason(
    text: str, needle: str
) -> None:
    with pytest.raises(FrontmatterError, match=re.escape(needle)):
        parse_definition(text, source="broken.md")


def test_crlf_frontmatter_parses_the_same_as_lf() -> None:
    """Files arrive from Windows editors; a \\r on the fence must not break loading."""
    definition = parse_definition(GOOD.replace("\n", "\r\n"), source="crlf.md")
    assert definition.tools == ("read", "glob", "write")


def test_every_shipped_model_role_is_one_the_router_knows() -> None:
    assert {"main", "plan", "fast"} == MODEL_ROLES
    for definition in BUILTIN_AGENTS.values():
        assert definition.model in MODEL_ROLES


# --------------------------------------------------------------------------- #
# Loading from disk
# --------------------------------------------------------------------------- #


def test_a_definition_loads_from_a_markdown_file_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "doc-writer.md"
    path.write_text(GOOD, encoding="utf-8")
    definition = load_definition(path)
    assert definition.name == "doc-writer"
    assert definition.source == str(path)
    assert definition.max_iterations == 6


def test_a_missing_agents_directory_is_normal_and_yields_nothing(tmp_path: Path) -> None:
    assert load_directory(tmp_path / "nope") == ()


def test_one_broken_file_fails_the_load_instead_of_disappearing(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_text(GOOD, encoding="utf-8")
    (tmp_path / "bad.md").write_text("---\nname: b\ntool: [read]\n---\nb\n", encoding="utf-8")
    with pytest.raises(FrontmatterError, match=re.escape("bad.md:3")):
        load_directory(tmp_path)


def test_a_file_overrides_a_builtin_of_the_same_name(tmp_path: Path) -> None:
    agents_dir = tmp_path / ".ronin" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: our house review\ntools: [read]\n---\n"
        "Only report BLOCKERs.\n",
        encoding="utf-8",
    )
    agents = load_agents(tmp_path)
    assert agents["reviewer"].description == "our house review"
    assert agents["reviewer"].tools == ("read",)
    assert set(BUILTIN_AGENTS) <= set(agents)


def test_the_catalogue_keeps_the_tool_layers_own_builtins(tmp_path: Path) -> None:
    catalogue = subagent_catalogue(load_agents(tmp_path))
    assert set(BUILTIN_SUBAGENTS) <= set(catalogue)
    assert set(BUILTIN_AGENTS) <= set(catalogue)


def test_a_definition_converts_into_the_subagent_type_the_task_tool_consumes() -> None:
    subagent = BUILTIN_AGENTS["fixer"].to_subagent_type()
    assert subagent.name == "fixer"
    assert subagent.max_iterations == 5
    assert "five attempts" in subagent.system_prompt


def test_the_explorer_prompt_demands_file_line_citations() -> None:
    assert "file:line" in BUILTIN_AGENTS["explorer"].system_prompt


def test_the_reviewer_reports_by_severity_and_cannot_edit() -> None:
    definition = BUILTIN_AGENTS["reviewer"]
    for severity in ("BLOCKER", "MAJOR", "MINOR", "NOTE"):
        assert severity in definition.system_prompt
    assert not {"write", "edit", "multi_edit", "bash"} & set(definition.tools)


def test_model_roles_reports_only_the_agents_that_ask_for_one(tmp_path: Path) -> None:
    roles = model_roles(load_agents(tmp_path))
    assert roles["explorer"] == "fast"
    assert roles["fixer"] == "main"


def test_gated_tools_names_what_a_child_policy_would_deny(tmp_path: Path) -> None:
    """`session.SubagentPolicy` denies gated calls, so wiring must know up front."""
    base = registry(tmp_path)
    assert gated_tools(BUILTIN_AGENTS["explorer"], base) == ()
    assert gated_tools(BUILTIN_AGENTS["test-writer"], base) == ("write", "edit", "multi_edit")


# --------------------------------------------------------------------------- #
# The path lane
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("relative", "allowed", "expected"),
    [
        ("tests/test_a.py", ("tests/**",), True),
        ("tests/unit/test_a.py", ("tests/**",), True),
        ("tests", ("tests/**",), True),
        ("src/app.py", ("tests/**",), False),
        ("testsuite/x.py", ("tests/**",), False),
        ("README.md", ("docs/**", "README.md"), True),
        ("docs/guide.md", ("docs/**", "README.md"), True),
        ("notes.md", ("docs/**", "README.md"), False),
    ],
)
def test_path_allowed_decides_by_glob(
    relative: str, allowed: tuple[str, ...], expected: bool
) -> None:
    assert path_allowed(relative, allowed) is expected


async def test_the_test_writer_cannot_write_to_src(tmp_path: Path) -> None:
    seed(tmp_path)
    lane = build_subagent_registry(registry(tmp_path), BUILTIN_AGENTS["test-writer"])
    result = await lane.execute(use("write", path="src/app.py", content="# hijacked\n"))
    assert not result.ok
    assert "tests/**" in result.error
    assert "src/app.py" in result.error
    assert (tmp_path / "src" / "app.py").read_bytes() == b"def handler():\n    return None\n"


async def test_the_test_writer_can_write_a_test(tmp_path: Path) -> None:
    seed(tmp_path)
    lane = build_subagent_registry(registry(tmp_path), BUILTIN_AGENTS["test-writer"])
    result = await lane.execute(
        use("write", path="tests/test_handler.py", content="def test_x() -> None:\n    pass\n")
    )
    assert result.ok
    assert (tmp_path / "tests" / "test_handler.py").exists()


async def test_the_restriction_also_covers_edit_and_multi_edit(tmp_path: Path) -> None:
    seed(tmp_path)
    lane = build_subagent_registry(registry(tmp_path), BUILTIN_AGENTS["test-writer"])
    edit = await lane.execute(use("edit", path="src/app.py", old_string="None", new_string="1"))
    multi = await lane.execute(
        use("multi_edit", path="src/app.py", edits=[{"old_string": "None", "new_string": "1"}])
    )
    assert not edit.ok and "tests/**" in edit.error
    assert not multi.ok and "tests/**" in multi.error


async def test_a_path_outside_the_tree_is_still_refused(tmp_path: Path) -> None:
    lane = build_subagent_registry(registry(tmp_path), BUILTIN_AGENTS["test-writer"])
    result = await lane.execute(use("write", path="../escaped.py", content="x"))
    assert not result.ok
    assert not (tmp_path.parent / "escaped.py").exists()


def test_the_restriction_is_stated_in_the_spec_the_model_sees(tmp_path: Path) -> None:
    lane = build_subagent_registry(registry(tmp_path), BUILTIN_AGENTS["test-writer"])
    spec = lane.get("write")
    assert spec is not None
    assert "PATH RESTRICTION" in spec.description
    assert "tests/**" in spec.description
    assert spec.danger_level is DangerLevel.MUTATING
    assert spec.requires_approval is True


def test_read_tools_are_not_wrapped(tmp_path: Path) -> None:
    base = registry(tmp_path)
    restricted = restrict_writes(base, ("tests/**",))
    assert restricted.tool("read") is base.tool("read")
    assert isinstance(restricted.tool("write"), RestrictedPaths)


def test_an_agent_without_write_paths_is_not_wrapped(tmp_path: Path) -> None:
    lane = build_subagent_registry(registry(tmp_path), BUILTIN_AGENTS["explorer"])
    assert not any(isinstance(tool, RestrictedPaths) for tool in lane.tools())


def test_a_restriction_with_no_allowed_globs_is_a_configuration_error() -> None:
    with pytest.raises(ValueError, match="refuse everything"):
        RestrictedPaths(WriteTool(), ())


def test_an_agent_naming_a_tool_the_session_lacks_fails_loudly(tmp_path: Path) -> None:
    definition = AgentDefinition(
        name="needs-net",
        description="fetches things",
        tools=("read", "web_fetch"),
        system_prompt="You fetch.",
    )
    with pytest.raises(ValueError, match="unknown tools"):
        build_subagent_registry(registry(tmp_path), definition)


async def test_the_wrapper_leaves_unrecognised_arguments_to_the_real_tool(
    tmp_path: Path,
) -> None:
    """Argument validation is the wrapped tool's job, not the wrapper's."""
    wrapped = RestrictedPaths(WriteTool(), ("tests/**",), label="test-writer")
    result = await wrapped.execute({"content": "no path here"}, context(tmp_path))
    assert not result.ok
    assert "path" in result.error


# --------------------------------------------------------------------------- #
# Dispatch fallback
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("where is the retry backoff handled in this repository", "explorer"),
        ("write a regression test for the CRLF bug", "test-writer"),
        ("review the diff on this branch for problems by severity", "reviewer"),
    ],
)
def test_dispatch_matches_a_task_against_the_descriptions(task: str, expected: str) -> None:
    chosen = select(task, subagent_catalogue(dict(BUILTIN_AGENTS), include_tool_builtins=False))
    assert chosen is not None
    assert chosen.name == expected


@pytest.mark.parametrize("task", ["order me a sandwich", "", "hmm"])
def test_dispatch_returns_none_rather_than_a_bad_guess(task: str) -> None:
    assert select(task, subagent_catalogue(dict(BUILTIN_AGENTS))) is None


def test_a_tie_is_not_broken_by_a_coin_flip() -> None:
    left = AgentDefinition(
        name="left", description="handles widgets", tools=("read",), system_prompt="x"
    ).to_subagent_type()
    right = AgentDefinition(
        name="right", description="handles widgets", tools=("read",), system_prompt="x"
    ).to_subagent_type()
    assert select("widgets please", [left, right]) is None


def test_rank_is_explainable_and_ordered_best_first() -> None:
    scores = rank(
        "write a regression test for the CRLF bug",
        subagent_catalogue(dict(BUILTIN_AGENTS), include_tool_builtins=False),
    )
    assert scores[0][0] == "test-writer"
    assert [score for _, score in scores] == sorted((score for _, score in scores), reverse=True)
