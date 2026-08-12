"""Skills, wired into a live session: the `skill` tool in the gated registry, and
`/name` dispatch through the interactive line session.

`tests/ext` owns the parser and the progressive-disclosure property in isolation. This
file owns the *joins* — that assembling a real :class:`~ronin.cli.sdk.Agent` over a
workspace with a skill on disk puts a working `skill` tool in the registry the model
sees, keeps its body out of the system prompt, and lets a human type `/name`. A fake
only at the provider seam, per this tree's rule for wiring tests.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import stream_harness as h

from ronin.cli.main import Slash, Streams, _slash
from ronin.cli.sdk import Agent
from ronin.core.types import Mode, ToolUse

BODY_MARKER = "RUN-THE-VERIFY-GATES-NOW"


def seed_skill(root: Path, name: str = "ship", description: str = "Release with gates.") -> Path:
    directory = root / ".ronin" / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: {name}
            description: {description}
            ---
            # {name}
            1. run the tests.
            2. {BODY_MARKER}
            """
        ),
        encoding="utf-8",
    )
    return root


async def open_agent(root: Path) -> Agent:
    router, _provider = h.scripted_router([h.provider_says("done.")])
    return await Agent.open(
        seed_skill(root),
        router=router,
        mode=Mode.AUTO_EDIT,
        home=root / "home",
        environ={},
        record=False,
        connect_mcp=False,
    )


def captured() -> tuple[Streams, list[str], list[str]]:
    out: list[str] = []
    err: list[str] = []
    streams = Streams(
        out=out.append, err=err.append, ask=lambda _q: "", flush=lambda: None, isatty=False
    )
    return streams, out, err


async def test_the_skill_tool_is_registered_and_returns_the_body(tmp_path: Path) -> None:
    agent = await open_agent(tmp_path)
    try:
        assert agent.runtime.registry.get("skill") is not None, "the skill tool must be wired"
        result = await agent.runtime.registry.execute(
            ToolUse(id="s1", name="skill", arguments={"name": "ship"})
        )
        assert result.ok, result.error
        assert BODY_MARKER in result.content
    finally:
        await agent.aclose()


async def test_the_catalog_is_in_the_system_prompt_but_the_body_is_not(tmp_path: Path) -> None:
    agent = await open_agent(tmp_path)
    try:
        system = agent.runtime.system
        assert "ship: Release with gates." in system
        assert BODY_MARKER not in system, "progressive disclosure holds through the live prompt"
    finally:
        await agent.aclose()


async def test_a_bare_workspace_still_ships_the_builtin_role_skills(tmp_path: Path) -> None:
    """No project/user skills, yet the built-in role workflows load, so the tool is here.

    `wire` adds `ext/role_skills/` as the lowest tier, so even an empty workspace has a
    non-empty catalog — and the `skill` tool with it.
    """
    router, _provider = h.scripted_router([h.provider_says("done.")])
    empty = tmp_path / "empty"
    empty.mkdir()
    agent = await Agent.open(
        empty,
        router=router,
        mode=Mode.AUTO_EDIT,
        home=tmp_path / "home",
        environ={},
        record=False,
        connect_mcp=False,
    )
    try:
        assert agent.runtime.registry.get("skill") is not None
        assert "autoplan" in agent.runtime.loaded.skills.names()
    finally:
        await agent.aclose()


async def test_no_skill_tool_when_the_skillset_is_genuinely_empty(tmp_path: Path) -> None:
    """The wiring invariant, tested at the one point it still holds.

    Builtins mean a normal `open` always has skills, so the "no always-denying tool over
    an empty catalog" branch is exercised directly: a `Loaded` with its skills replaced
    by an empty set builds a runtime with no `skill` tool.
    """
    from dataclasses import replace

    from ronin.cli.spine import Paths
    from ronin.cli.wire import build_runtime, load_workspace
    from ronin.ext.skills import SkillSet

    router, _provider = h.scripted_router([h.provider_says("done.")])
    empty = tmp_path / "empty"
    empty.mkdir()
    loaded = load_workspace(Paths(workspace_root=empty, home=tmp_path / "home", cwd=empty))
    runtime = await build_runtime(
        replace(loaded, skills=SkillSet()), router, connect_mcp=False, record=False
    )
    try:
        assert runtime.registry.get("skill") is None
    finally:
        await runtime.aclose()


async def test_a_human_can_invoke_a_skill_by_slash_name(tmp_path: Path) -> None:
    agent = await open_agent(tmp_path)
    streams, out, _err = captured()
    try:
        outcome = await _slash("/ship", agent, streams)
        assert isinstance(outcome, Slash)
        assert BODY_MARKER in outcome.prompt, "the body runs as the next turn"
        assert any("skill" in line for line in out)
    finally:
        await agent.aclose()


async def test_a_builtin_command_is_never_shadowed_by_a_skill(tmp_path: Path) -> None:
    """A skill named `plan` cannot capture `/plan` — the builtin parses first."""
    root = tmp_path
    seed_skill(root, name="plan", description="a skill that must not win")
    router, _provider = h.scripted_router([h.provider_says("done.")])
    agent = await Agent.open(
        root,
        router=router,
        mode=Mode.AUTO_EDIT,
        home=root / "home",
        environ={},
        record=False,
        connect_mcp=False,
    )
    streams, out, _err = captured()
    try:
        outcome = await _slash("/plan", agent, streams)
        # The builtin /plan entered plan mode; it did not hand back the skill body.
        assert outcome.prompt == ""
        assert BODY_MARKER not in "".join(out)
        assert any("plan mode" in line for line in out)
    finally:
        await agent.aclose()
