"""The built-in role-workflow skills that ship inside ``ronin.ext.role_skills``.

These are not fixtures on a ``tmp_path`` — they are the real files a released Ronin
carries, so this test loads them the way a session does (:func:`load_skill_dir`) and
asserts the properties the spec promises: every one parses, every name is valid and
clear of the built-in slash commands, attribution travels with each, and the tools and
subagents a body claims to orchestrate are the real ones Ronin actually has.

The per-skill checks are parametrized over whatever is discovered on disk, so a skill
added under ``role_skills/`` is covered the moment its ``SKILL.md`` lands — no edit here.
Offline and model-free: the skills are text, and everything imported is pure data.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import ronin.ext
from ronin.agents.definitions import BUILTIN_AGENTS
from ronin.ext.skills import discover, load_skill_dir
from ronin.tools.base import Tool
from ronin.tools.files import EditTool, MultiEditTool, ReadTool, WriteTool
from ronin.tools.net import WebFetchTool, WebSearchTool
from ronin.tools.search import GlobTool, GrepTool, LsTool
from ronin.tools.shell import BashOutputTool, BashTool
from ronin.tools.task import TaskTool, TodoWriteTool
from ronin.ui.commands import BUILTIN_COMMANDS

# The real Ronin tool set, read off the tool classes themselves rather than restated —
# rename a tool and this set follows, so "orchestrates real tools" stays a live claim.
_TOOL_CLASSES: tuple[type[Tool], ...] = (
    ReadTool,
    WriteTool,
    EditTool,
    MultiEditTool,
    GlobTool,
    GrepTool,
    LsTool,
    BashTool,
    BashOutputTool,
    WebFetchTool,
    WebSearchTool,
    TodoWriteTool,
    TaskTool,
)
RONIN_TOOL_NAMES: frozenset[str] = frozenset(cls.name for cls in _TOOL_CLASSES)

# The names a skill may not take: the built-in slash commands win at dispatch, so a
# skill sharing one could never be invoked by ``/name``. Imported, not hand-listed.
BUILTIN_COMMAND_NAMES: frozenset[str] = frozenset(command.name for command in BUILTIN_COMMANDS)

# Every skill the spec requires this pack to ship.
EXPECTED_SKILLS: frozenset[str] = frozenset(
    {
        "autoplan",
        "office-hours",
        "design-review",
        "eng-review",
        "review",
        "ship",
        "qa",
        "retro",
        "investigate",
    }
)

# Derive the directory from the package, never a hardcoded absolute path.
_EXT_INIT = ronin.ext.__file__
assert _EXT_INIT is not None, "ronin.ext must be an importable package with a file"
ROLE_SKILLS_DIR: Path = Path(_EXT_INIT).parent / "role_skills"

# Discovered once at import; the per-skill tests parametrize over it so a new skill
# directory is auto-covered.
SKILL_DIRS: tuple[Path, ...] = tuple(discover(ROLE_SKILLS_DIR))
SKILL_IDS: tuple[str, ...] = tuple(directory.name for directory in SKILL_DIRS)

_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# --------------------------------------------------------------------------- #
# the pack as a whole
# --------------------------------------------------------------------------- #


def test_the_role_skills_directory_ships_skills() -> None:
    assert ROLE_SKILLS_DIR.is_dir(), f"{ROLE_SKILLS_DIR} is missing"
    assert SKILL_DIRS, "no SKILL.md directories were discovered under role_skills"


def test_the_expected_skill_set_is_exactly_present() -> None:
    """Not a subset — equality, so a missing skill and a stray one both fail here."""
    assert set(SKILL_IDS) == EXPECTED_SKILLS


def test_there_are_thirteen_builtin_commands_to_avoid() -> None:
    """A guard on the collision check: if the command set changes, revisit the names."""
    assert len(BUILTIN_COMMAND_NAMES) == 13
    assert "plan" in BUILTIN_COMMAND_NAMES, "so the planning skill is 'autoplan', not 'plan'"


# --------------------------------------------------------------------------- #
# every discovered skill, one parameter each
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("directory", SKILL_DIRS, ids=SKILL_IDS)
def test_skill_loads_cleanly_with_attribution(directory: Path) -> None:
    """load_skill_dir succeeds: valid frontmatter, valid name, non-empty body, credit."""
    skill = load_skill_dir(directory, source="builtin")
    assert _VALID_NAME.match(skill.name), f"invalid skill name {skill.name!r}"
    assert skill.name == directory.name, "a skill's name should match its directory"
    assert skill.description.strip(), "the catalog line needs a description"
    assert skill.body.strip(), "the body is the skill; it must be non-empty"
    assert skill.adapted_from, "a ported workflow must carry adapted-from attribution"
    assert skill.license, "a ported workflow must name its source license"


@pytest.mark.parametrize("directory", SKILL_DIRS, ids=SKILL_IDS)
def test_skill_name_does_not_collide_with_a_builtin_command(directory: Path) -> None:
    skill = load_skill_dir(directory, source="builtin")
    assert skill.name not in BUILTIN_COMMAND_NAMES, (
        f"{skill.name!r} collides with a builtin slash command, which wins at dispatch"
    )


@pytest.mark.parametrize("directory", SKILL_DIRS, ids=SKILL_IDS)
def test_allowed_tools_are_all_real_ronin_tools(directory: Path) -> None:
    """The spec's orchestration claim: every declared tool is one Ronin actually has."""
    skill = load_skill_dir(directory, source="builtin")
    assert skill.allowed_tools, "a workflow skill declares the tools it orchestrates"
    unknown = set(skill.allowed_tools) - RONIN_TOOL_NAMES
    assert not unknown, f"{skill.name} declares tools that do not exist: {sorted(unknown)}"


# --------------------------------------------------------------------------- #
# the two skills that orchestrate a subagent by name
# --------------------------------------------------------------------------- #


def test_review_orchestrates_the_reviewer_subagent_via_task() -> None:
    skill = load_skill_dir(ROLE_SKILLS_DIR / "review", source="builtin")
    assert "task" in skill.allowed_tools, "review delegates via the task tool"
    assert "task" in skill.body, "the body must tell the model to use task"
    assert "reviewer" in skill.body, "the body must name the reviewer subagent"
    assert "reviewer" in BUILTIN_AGENTS, "and 'reviewer' must be a real subagent"


def test_investigate_orchestrates_the_fixer_subagent_via_task() -> None:
    skill = load_skill_dir(ROLE_SKILLS_DIR / "investigate", source="builtin")
    assert "task" in skill.allowed_tools, "investigate delegates via the task tool"
    assert "task" in skill.body, "the body must tell the model to use task"
    assert "fixer" in skill.body, "the body must name the fixer subagent"
    assert "fixer" in BUILTIN_AGENTS, "and 'fixer' must be a real subagent"
