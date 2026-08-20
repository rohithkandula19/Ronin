"""Skills: parsing, discovery precedence, and the property the whole feature is for.

The load-bearing test is :func:`test_a_skill_body_is_absent_until_invoked_then_present`
— progressive disclosure, asserted the way the spec words it: the body is *not* in what
the model sees at session start, and *is* after the skill is invoked. Everything else
here is the parser and the precedence rules that a 600-skill catalog depends on staying
cheap and predictable.

Offline and model-free: skills are text on disk, so every test runs on a ``tmp_path``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ronin.cli.spine import Paths
from ronin.cli.wire import load_workspace, system_prompt
from ronin.ext.skills import (
    SKILL_FILENAME,
    Skill,
    SkillError,
    SkillSet,
    SkillSource,
    SkillTool,
    load_skills,
    parse_skill,
)
from ronin.tools.base import ToolContext

BODY_MARKER = "STEP-TWO-WRITE-THE-PLAN"  # a phrase that lives only in a skill body


def write_skill(
    skills_root: Path,
    name: str,
    *,
    description: str = "A saved workflow.",
    body: str = f"# Steps\n1. read the test.\n2. {BODY_MARKER}.\n",
    extra_frontmatter: str = "",
    companions: dict[str, str] | None = None,
) -> Path:
    """Lay down ``<skills_root>/<name>/SKILL.md`` plus any companion files."""
    directory = skills_root / name
    directory.mkdir(parents=True, exist_ok=True)
    front = f"name: {name}\ndescription: {description}\n{extra_frontmatter}".rstrip()
    (directory / SKILL_FILENAME).write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")
    for rel, text in (companions or {}).items():
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory


# --------------------------------------------------------------------------- #
# progressive disclosure — the point of the feature
# --------------------------------------------------------------------------- #


async def test_a_skill_body_is_absent_until_invoked_then_present(tmp_path: Path) -> None:
    """The spec, verbatim: body NOT in context until invoked, then IS after."""
    write_skill(tmp_path / ".ronin" / "skills", "autoplan", description="Plan before coding.")
    skills, notes = load_skills(tmp_path, tmp_path / "home")
    assert notes == ()
    assert skills.names() == ("autoplan",)

    catalog = skills.catalog()
    assert "autoplan: Plan before coding." in catalog, "the one-line description must be present"
    assert BODY_MARKER not in catalog, "the body must NOT be in the session-start catalog"

    result = await SkillTool(skills).run({"name": "autoplan"}, ToolContext(root=tmp_path))
    assert result.ok
    assert BODY_MARKER in result.content, "invoking the skill must load its body"


async def test_the_body_is_absent_from_the_assembled_system_prompt(tmp_path: Path) -> None:
    """The same property one layer up: through the real workspace loader and prompt.

    ``load_workspace`` → ``Loaded.system_suffix`` → ``system_prompt`` is the exact path a
    live session's stable prefix takes, so this is the claim as the model would meet it.
    """
    write_skill(tmp_path / ".ronin" / "skills", "myreview", description="Review a diff.")
    paths = Paths(workspace_root=tmp_path, home=tmp_path / "home", cwd=tmp_path)
    loaded = load_workspace(paths, environ={})
    prompt = system_prompt(loaded)
    assert "myreview: Review a diff." in prompt
    assert BODY_MARKER not in prompt
    # The project skill loads alongside the built-in role workflows, which `wire` adds
    # as the lowest tier — so the set is "mine plus the builtins", not "mine alone".
    assert "myreview" in loaded.skills.names()
    assert "autoplan" in loaded.skills.names(), "built-in role skills ship by default"


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def test_parse_reads_every_optional_key(tmp_path: Path) -> None:
    text = textwrap.dedent(
        """\
        ---
        name: eng-review
        description: Rate the plan across dimensions.
        allowed-tools: [read, grep]
        model: plan
        adapted-from: gstack/eng-review
        license: MIT
        ---
        # Engineering review
        Rate each dimension 0-10.
        """
    )
    skill = parse_skill(text, source="user", directory=tmp_path)
    assert skill.name == "eng-review"
    assert skill.allowed_tools == ("read", "grep")
    assert skill.model == "plan"
    assert skill.adapted_from == "gstack/eng-review"
    assert skill.license == "MIT"
    assert "Rate each dimension" in skill.body


def test_a_misspelled_key_is_a_load_error_not_a_silent_drop(tmp_path: Path) -> None:
    """`allowed_tools` (underscore) is the wrong spelling; catching it is the point."""
    text = "---\nname: x\ndescription: d\nallowed_tools: [read]\n---\nbody\n"
    with pytest.raises(SkillError, match="unknown key 'allowed_tools'"):
        parse_skill(text, source="s", directory=tmp_path)


def test_a_missing_required_key_is_named(tmp_path: Path) -> None:
    text = "---\nname: x\n---\nbody\n"
    with pytest.raises(SkillError, match=r"missing required key.*description"):
        parse_skill(text, source="s", directory=tmp_path)


def test_an_empty_body_is_refused_because_the_body_is_the_skill(tmp_path: Path) -> None:
    text = "---\nname: x\ndescription: d\n---\n"
    with pytest.raises(SkillError, match="no body"):
        parse_skill(text, source="s", directory=tmp_path)


def test_a_bad_name_is_refused(tmp_path: Path) -> None:
    text = "---\nname: Not A Name\ndescription: d\n---\nbody\n"
    with pytest.raises(SkillError, match="must be lowercase"):
        parse_skill(text, source="s", directory=tmp_path)


# --------------------------------------------------------------------------- #
# discovery precedence — local wins
# --------------------------------------------------------------------------- #


def test_project_shadows_user_and_the_shadow_is_a_note(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write_skill(home / ".ronin" / "skills", "plan", description="user copy")
    write_skill(tmp_path / ".ronin" / "skills", "plan", description="project copy")
    skills, notes = load_skills(tmp_path, home)
    winner = skills.get("plan")
    assert winner is not None
    assert winner.description == "project copy", "project (nearer) must win"
    assert any("shadows the one from user" in note for note in notes)


def test_installed_packages_are_the_lowest_tier(tmp_path: Path) -> None:
    """A plugin's skill is overridden by both the user's and the project's."""
    home = tmp_path / "home"
    installed = tmp_path / "pkg" / "skills"
    write_skill(installed, "review", description="from a plugin")
    write_skill(home / ".ronin" / "skills", "review", description="from the user")
    skills, _notes = load_skills(
        tmp_path, home, extra=[SkillSource(root=installed, label="plugin:demo")]
    )
    winner = skills.get("review")
    assert winner is not None
    assert winner.description == "from the user"


def test_an_installed_skill_survives_when_nothing_shadows_it(tmp_path: Path) -> None:
    installed = tmp_path / "pkg" / "skills"
    write_skill(installed, "qa", description="drive a url")
    skills, _ = load_skills(
        tmp_path, tmp_path / "home", extra=[SkillSource(root=installed, label="plugin:demo")]
    )
    assert skills.names() == ("qa",)
    survivor = skills.get("qa")
    assert survivor is not None
    assert survivor.source == "plugin:demo"


def test_a_malformed_skill_is_skipped_with_a_note_not_a_crash(tmp_path: Path) -> None:
    root = tmp_path / ".ronin" / "skills"
    write_skill(root, "good")
    bad = root / "bad"
    bad.mkdir(parents=True)
    (bad / SKILL_FILENAME).write_text("no frontmatter here\n", encoding="utf-8")
    skills, notes = load_skills(tmp_path, tmp_path / "home")
    assert skills.names() == ("good",), "the good skill still loads"
    assert any("bad" in note and "skipped" in note for note in notes)


def test_a_directory_without_a_skill_file_is_not_a_skill(tmp_path: Path) -> None:
    root = tmp_path / ".ronin" / "skills"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "README.md").write_text("not a skill\n", encoding="utf-8")
    skills, notes = load_skills(tmp_path, tmp_path / "home")
    assert skills.names() == ()
    assert notes == ()


# --------------------------------------------------------------------------- #
# companion files and rendering
# --------------------------------------------------------------------------- #


def test_companion_files_are_listed_relative_and_on_demand(tmp_path: Path) -> None:
    directory = write_skill(
        tmp_path / ".ronin" / "skills",
        "ship",
        companions={"scripts/release.sh": "echo go\n", "checklist.md": "- tag\n"},
    )
    skill = load_skills(tmp_path, tmp_path / "home")[0].get("ship")
    assert skill is not None
    assert skill.companion_files() == ("checklist.md", "scripts/release.sh")
    rendered = skill.render()
    assert "scripts/release.sh" in rendered and "checklist.md" in rendered
    assert str(directory) in rendered, "the resolving directory is named for the model"


def test_dotfiles_are_not_companions(tmp_path: Path) -> None:
    write_skill(
        tmp_path / ".ronin" / "skills",
        "qa",
        companions={".hidden": "x\n", "keep.txt": "y\n"},
    )
    skill = load_skills(tmp_path, tmp_path / "home")[0].get("qa")
    assert skill is not None
    assert skill.companion_files() == ("keep.txt",)


def test_render_carries_attribution_for_a_ported_workflow(tmp_path: Path) -> None:
    write_skill(
        tmp_path / ".ronin" / "skills",
        "investigate",
        extra_frontmatter="adapted-from: karpathy/investigate\nlicense: Apache-2.0",
    )
    skill = load_skills(tmp_path, tmp_path / "home")[0].get("investigate")
    assert skill is not None
    assert "adapted from karpathy/investigate (Apache-2.0)" in skill.render()


# --------------------------------------------------------------------------- #
# invocation shapes
# --------------------------------------------------------------------------- #


def test_slash_invocation_matches_only_a_known_skill() -> None:
    skills = SkillSet(
        skills=(Skill(name="plan", description="d", body="do the plan\n", directory=Path(".")),)
    )
    assert skills.invocation("/nope") is None
    assert skills.invocation("not a slash") is None
    hit = skills.invocation("/plan add auth")
    assert hit is not None
    assert hit.skill.name == "plan"
    assert hit.argument == "add auth"


def test_argument_substitutes_into_arguments_placeholder() -> None:
    skill = Skill(name="x", description="d", body="Plan for: $ARGUMENTS\n", directory=Path("."))
    invocation = SkillSet(skills=(skill,)).invocation("/x ship it")
    assert invocation is not None
    assert "Plan for: ship it" in invocation.prompt
    assert "User input:" not in invocation.prompt, "no double-append when a placeholder exists"


def test_argument_is_appended_when_the_body_has_no_placeholder() -> None:
    skill = Skill(name="x", description="d", body="Do the thing.\n", directory=Path("."))
    invocation = SkillSet(skills=(skill,)).invocation("/x now")
    assert invocation is not None
    assert invocation.prompt.rstrip().endswith("User input: now")


# --------------------------------------------------------------------------- #
# the tool's error path
# --------------------------------------------------------------------------- #


async def test_the_tool_names_the_available_skills_when_asked_for_an_unknown_one(
    tmp_path: Path,
) -> None:
    write_skill(tmp_path / ".ronin" / "skills", "plan")
    write_skill(tmp_path / ".ronin" / "skills", "review")
    skills = load_skills(tmp_path, tmp_path / "home")[0]
    result = await SkillTool(skills).run({"name": "nonesuch"}, ToolContext(root=tmp_path))
    assert not result.ok
    assert "no skill called 'nonesuch'" in result.error
    assert "plan" in result.error and "review" in result.error


async def test_the_tool_rejects_a_missing_name(tmp_path: Path) -> None:
    result = await SkillTool(SkillSet()).run({}, ToolContext(root=tmp_path))
    assert not result.ok
    assert "'name' is required" in result.error


def test_an_empty_skillset_has_an_empty_catalog() -> None:
    assert SkillSet().catalog() == ""
    assert SkillSet().names() == ()
