"""Plugins: one manifest, five reused surfaces, and errors that become notes.

The load-bearing test is :func:`test_collect_surfaces_registers_every_surface` — the
spec's "install a fixture plugin; assert every surface registers": a single bundle
carrying a skill, an MCP server, a subagent, a command and a hook, and the assertion
that :func:`collect_surfaces` yields all five. The rest cover manifest parsing (both
manifest directories, the required ``name``, forward-compatible unknown keys, the trust
default), install/discovery under the home layer, the malformed-surface-becomes-a-note
contract, and the marketplace listing that feeds a local install.

Offline and model-free: a plugin is files on disk, so every test runs on ``tmp_path``
and nothing connects to a server, spawns a hook, or reaches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ronin.ext.plugins import (
    DEFAULT_TRUST,
    PLUGINS_SUBDIR,
    Marketplace,
    Plugin,
    PluginError,
    PluginSurfaces,
    collect_surfaces,
    install,
    load_installed,
    load_marketplace,
    load_plugin,
)
from ronin.ext.skills import load_skills

# Sentinels a test can look for to prove a specific fixture surface made it through.
SKILL_NAME = "demo-skill"
MCP_SERVER_NAME = "demo-docs"
AGENT_NAME = "demo-reviewer"
COMMAND_NAME = "demo-cmd"
HOOK_COMMAND = "echo demo-hook-ran"


def write_plugin(
    root: Path,
    name: str,
    *,
    skills: tuple[str, ...] = (SKILL_NAME,),
    mcp: bool = True,
    agents: tuple[str, ...] = (AGENT_NAME,),
    commands: tuple[str, ...] = (COMMAND_NAME,),
    hooks: bool = True,
    trust: str = DEFAULT_TRUST,
    manifest_dir: str = ".ronin-plugin",
) -> Path:
    """Lay down a full plugin directory under ``root/<name>`` and return its path.

    Every surface is on by default so one call produces a bundle exercising all of them;
    pass an empty tuple / ``False`` to omit a surface (an empty plugin is
    ``skills=(), agents=(), commands=(), mcp=False, hooks=False``).
    """
    directory = root / name
    (directory / manifest_dir).mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.2.3",
        "description": "A demo plugin.",
        "trust": trust,
    }
    (directory / manifest_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    for skill in skills:
        skill_dir = directory / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: A saved workflow.\n---\n# {skill}\ndo the thing.\n",
            encoding="utf-8",
        )

    for agent in agents:
        agent_dir = directory / "agents"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / f"{agent}.md").write_text(
            f"---\nname: {agent}\ndescription: Reviews the diff carefully.\n"
            f"tools: [read, grep]\n---\nYou are the {agent} subagent.\n",
            encoding="utf-8",
        )

    for command in commands:
        command_dir = directory / "commands"
        command_dir.mkdir(parents=True, exist_ok=True)
        (command_dir / f"{command}.md").write_text(
            f"# run {command}\nDo {command} on $ARGUMENTS\n", encoding="utf-8"
        )

    if mcp:
        (directory / ".mcp.json").write_text(
            json.dumps(
                {"mcpServers": {MCP_SERVER_NAME: {"transport": "stdio", "command": "echo"}}}
            ),
            encoding="utf-8",
        )

    if hooks:
        (directory / "hooks.json").write_text(
            json.dumps(
                {"PreToolUse": [{"matcher": "write", "hooks": [{"command": HOOK_COMMAND}]}]}
            ),
            encoding="utf-8",
        )
    return directory


# --------------------------------------------------------------------------- #
# the spec's required test: every surface registers
# --------------------------------------------------------------------------- #


def test_collect_surfaces_registers_every_surface(tmp_path: Path) -> None:
    """Install a fixture plugin; assert the skill, server, agent, command and hook register."""
    directory = write_plugin(tmp_path, "kitchen-sink")
    plugin = load_plugin(directory)

    surfaces = collect_surfaces([plugin])
    assert surfaces.notes == (), "a well-formed plugin contributes no skip notes"

    # skills come out as a source; loading through it (lowest tier) yields the skill.
    assert len(surfaces.skill_sources) == 1
    assert surfaces.skill_sources[0].label == "plugin:kitchen-sink"
    skillset, skill_notes = load_skills(
        tmp_path / "proj", tmp_path / "home", extra=surfaces.skill_sources
    )
    assert skill_notes == ()
    assert SKILL_NAME in skillset.names()

    assert tuple(server.name for server in surfaces.mcp_servers) == (MCP_SERVER_NAME,)
    assert tuple(agent.name for agent in surfaces.agents) == (AGENT_NAME,)
    assert tuple(command.command.name for command in surfaces.commands) == (COMMAND_NAME,)
    assert tuple(spec.command for spec in surfaces.hooks) == (HOOK_COMMAND,)


# --------------------------------------------------------------------------- #
# manifest parsing
# --------------------------------------------------------------------------- #


def test_both_manifest_directories_are_accepted(tmp_path: Path) -> None:
    """`.ronin-plugin` is native; `.codex-plugin` is the alias — both load."""
    native = write_plugin(tmp_path, "native", manifest_dir=".ronin-plugin")
    codex = write_plugin(tmp_path, "codex", manifest_dir=".codex-plugin")
    assert load_plugin(native).name == "native"
    assert load_plugin(codex).name == "codex"


def test_ronin_manifest_wins_when_both_directories_are_present(tmp_path: Path) -> None:
    """`.ronin-plugin` is tried first, so it wins over a `.codex-plugin` alias."""
    directory = write_plugin(tmp_path, "dual", manifest_dir=".ronin-plugin")
    (directory / ".codex-plugin").mkdir()
    (directory / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "wrong-one"}), encoding="utf-8"
    )
    assert load_plugin(directory).name == "dual"


def test_a_missing_manifest_is_a_plugin_error(tmp_path: Path) -> None:
    (tmp_path / "not-a-plugin").mkdir()
    with pytest.raises(PluginError, match="no manifest"):
        load_plugin(tmp_path / "not-a-plugin")


def test_a_missing_name_is_a_plugin_error(tmp_path: Path) -> None:
    directory = tmp_path / "nameless"
    (directory / ".ronin-plugin").mkdir(parents=True)
    (directory / ".ronin-plugin" / "plugin.json").write_text(
        json.dumps({"version": "1.0"}), encoding="utf-8"
    )
    with pytest.raises(PluginError, match="'name' is required"):
        load_plugin(directory)


def test_a_bad_name_is_a_plugin_error(tmp_path: Path) -> None:
    directory = tmp_path / "badname"
    (directory / ".ronin-plugin").mkdir(parents=True)
    (directory / ".ronin-plugin" / "plugin.json").write_text(
        json.dumps({"name": "Not A Name"}), encoding="utf-8"
    )
    with pytest.raises(PluginError, match="must be lowercase"):
        load_plugin(directory)


def test_unknown_keys_are_ignored_for_forward_compatibility(tmp_path: Path) -> None:
    directory = tmp_path / "future"
    (directory / ".ronin-plugin").mkdir(parents=True)
    (directory / ".ronin-plugin" / "plugin.json").write_text(
        json.dumps({"name": "future", "invented_by_a_newer_ronin": {"x": 1}}), encoding="utf-8"
    )
    plugin = load_plugin(directory)
    assert plugin.name == "future"


def test_trust_defaults_to_community_when_absent(tmp_path: Path) -> None:
    directory = tmp_path / "untrusted"
    (directory / ".ronin-plugin").mkdir(parents=True)
    (directory / ".ronin-plugin" / "plugin.json").write_text(
        json.dumps({"name": "untrusted"}), encoding="utf-8"
    )
    assert load_plugin(directory).trust == "community"


def test_an_unknown_trust_tier_is_a_plugin_error(tmp_path: Path) -> None:
    """A typo'd tier is refused rather than silently downgraded to community."""
    directory = write_plugin(tmp_path, "typo", trust="trused")
    with pytest.raises(PluginError, match="'trust' must be one of"):
        load_plugin(directory)


def test_version_and_description_are_read(tmp_path: Path) -> None:
    plugin = load_plugin(write_plugin(tmp_path, "described"))
    assert plugin.version == "1.2.3"
    assert plugin.description == "A demo plugin."


# --------------------------------------------------------------------------- #
# install + discovery under the home layer
# --------------------------------------------------------------------------- #


def test_install_copies_a_local_source_into_the_home_layer(tmp_path: Path) -> None:
    source = write_plugin(tmp_path / "sources", "portable")
    home = tmp_path / "home"

    installed = install(source, home)
    expected = home / PLUGINS_SUBDIR / "portable"
    assert installed.directory == expected
    assert (expected / ".ronin-plugin" / "plugin.json").is_file(), "the manifest was copied"
    assert (expected / "skills" / SKILL_NAME / "SKILL.md").is_file(), "a surface was copied too"
    # the installed copy loads and still carries every surface
    assert collect_surfaces([installed]).mcp_servers[0].name == MCP_SERVER_NAME


def test_load_installed_finds_an_installed_plugin(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install(write_plugin(tmp_path / "sources", "findme"), home)

    plugins, notes = load_installed(home)
    assert notes == ()
    assert tuple(plugin.name for plugin in plugins) == ("findme",)


def test_load_installed_is_empty_when_no_plugins_dir_exists(tmp_path: Path) -> None:
    plugins, notes = load_installed(tmp_path / "empty-home")
    assert plugins == ()
    assert notes == ()


def test_install_replaces_an_existing_plugin_of_the_same_name(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install(write_plugin(tmp_path / "v1", "app", commands=("old-cmd",)), home)
    install(write_plugin(tmp_path / "v2", "app", commands=("new-cmd",)), home)

    plugins, _ = load_installed(home)
    assert len(plugins) == 1, "the reinstall replaced rather than duplicated"
    assert tuple(c.command.name for c in plugins[0].commands()) == ("new-cmd",)


def test_install_can_refuse_to_replace(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install(write_plugin(tmp_path / "v1", "app"), home)
    with pytest.raises(PluginError, match="already installed"):
        install(write_plugin(tmp_path / "v2", "app"), home, replace=False)


def test_load_installed_skips_a_malformed_plugin_with_a_note(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install(write_plugin(tmp_path / "sources", "good"), home)
    # a directory under plugins/ that is not a plugin (no manifest)
    (home / PLUGINS_SUBDIR / "junk").mkdir(parents=True)

    plugins, notes = load_installed(home)
    assert tuple(plugin.name for plugin in plugins) == ("good",)
    assert any("junk" in note and "skipped" in note for note in notes)


# --------------------------------------------------------------------------- #
# errors are values: a malformed surface is a note, the rest still register
# --------------------------------------------------------------------------- #


def test_a_malformed_surface_becomes_a_note_and_the_others_survive(tmp_path: Path) -> None:
    directory = write_plugin(tmp_path, "half-broken")
    # clobber the hooks file with invalid JSON — the one surface that should drop out.
    (directory / "hooks.json").write_text("{ not json", encoding="utf-8")
    plugin = load_plugin(directory)  # the manifest is fine, so the plugin still loads

    surfaces = collect_surfaces([plugin])
    assert surfaces.hooks == (), "the broken hooks surface contributed nothing"
    assert any("hooks.json" in note for note in surfaces.notes), "and it is named in a note"
    # every other surface still registered
    assert surfaces.mcp_servers and surfaces.agents and surfaces.commands and surfaces.skill_sources


def test_a_malformed_mcp_surface_is_isolated(tmp_path: Path) -> None:
    directory = write_plugin(tmp_path, "bad-mcp")
    (directory / ".mcp.json").write_text(
        json.dumps({"mcpServers": "not-an-object"}), encoding="utf-8"
    )
    surfaces = collect_surfaces([load_plugin(directory)])
    assert surfaces.mcp_servers == ()
    assert any(".mcp.json" in note for note in surfaces.notes)
    assert surfaces.agents and surfaces.hooks


def test_the_surface_error_raised_directly_is_a_plugin_error(tmp_path: Path) -> None:
    """Called on its own (not through collect_surfaces) a bad surface raises."""
    directory = write_plugin(tmp_path, "raises")
    (directory / "hooks.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(PluginError, match=r"hooks\.json"):
        load_plugin(directory).hooks()


# --------------------------------------------------------------------------- #
# empty plugin: all-empty surfaces, no notes
# --------------------------------------------------------------------------- #


def test_an_empty_plugin_yields_all_empty_surfaces_and_no_notes(tmp_path: Path) -> None:
    directory = write_plugin(
        tmp_path, "bare", skills=(), agents=(), commands=(), mcp=False, hooks=False
    )
    plugin = load_plugin(directory)
    assert plugin.skill_source() is None
    assert plugin.assets_dir() is None

    surfaces = collect_surfaces([plugin])
    assert surfaces == PluginSurfaces(), "an empty plugin contributes nothing at all"


def test_assets_dir_is_recorded_but_not_parsed(tmp_path: Path) -> None:
    directory = write_plugin(tmp_path, "with-assets")
    (directory / "assets").mkdir()
    (directory / "assets" / "logo.bin").write_bytes(b"\x00\x01\x02")
    plugin = load_plugin(directory)
    assets = plugin.assets_dir()
    assert assets == directory / "assets"


# --------------------------------------------------------------------------- #
# marketplace
# --------------------------------------------------------------------------- #


def test_load_marketplace_parses_a_listing(tmp_path: Path) -> None:
    listing = tmp_path / "marketplace.json"
    listing.write_text(
        json.dumps(
            {
                "plugins": [
                    {"name": "alpha", "source": "./alpha", "description": "the first"},
                    {"name": "beta", "source": "https://example.invalid/beta.git"},
                ]
            }
        ),
        encoding="utf-8",
    )
    market = load_marketplace(listing)
    assert isinstance(market, Marketplace)
    assert tuple(entry.name for entry in market.entries) == ("alpha", "beta")
    assert market.get("alpha") is not None
    assert market.get("alpha").description == "the first"  # type: ignore[union-attr]
    assert market.get("beta").is_url is True  # type: ignore[union-attr]


def test_a_marketplace_entry_feeds_a_local_install(tmp_path: Path) -> None:
    """The end-to-end local path: a listing points at a source that install consumes."""
    # a marketplace folder with a plugin checked in beside its index
    market_dir = tmp_path / "market"
    write_plugin(market_dir, "shipped")
    (market_dir / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "shipped", "source": "./shipped"}]}), encoding="utf-8"
    )

    market = load_marketplace(market_dir / "marketplace.json")
    entry = market.get("shipped")
    assert entry is not None
    source = market.local_source(entry)

    home = tmp_path / "home"
    installed = install(source, home)
    assert installed.name == "shipped"
    plugins, _ = load_installed(home)
    assert tuple(plugin.name for plugin in plugins) == ("shipped",)


def test_local_source_refuses_a_url(tmp_path: Path) -> None:
    listing = tmp_path / "marketplace.json"
    listing.write_text(
        json.dumps({"plugins": [{"name": "remote", "source": "https://example.invalid/x.git"}]}),
        encoding="utf-8",
    )
    market = load_marketplace(listing)
    entry = market.get("remote")
    assert entry is not None
    with pytest.raises(PluginError, match="clone it to a local path"):
        market.local_source(entry)


def test_a_marketplace_entry_missing_source_is_rejected(tmp_path: Path) -> None:
    listing = tmp_path / "marketplace.json"
    listing.write_text(json.dumps({"plugins": [{"name": "x"}]}), encoding="utf-8")
    with pytest.raises(PluginError, match="'source' is required"):
        load_marketplace(listing)


def test_an_empty_marketplace_object_has_no_entries(tmp_path: Path) -> None:
    listing = tmp_path / "marketplace.json"
    listing.write_text(json.dumps({}), encoding="utf-8")
    assert load_marketplace(listing).entries == ()


# --------------------------------------------------------------------------- #
# a couple of Plugin-level surface checks
# --------------------------------------------------------------------------- #


def test_precedence_order_is_preserved_across_plugins(tmp_path: Path) -> None:
    """collect_surfaces keeps the caller's plugin order — the order precedence needs."""
    first = load_plugin(
        write_plugin(
            tmp_path / "a",
            "first",
            agents=("agent-a",),
            skills=(),
            mcp=False,
            commands=(),
            hooks=False,
        )
    )
    second = load_plugin(
        write_plugin(
            tmp_path / "b",
            "second",
            agents=("agent-b",),
            skills=(),
            mcp=False,
            commands=(),
            hooks=False,
        )
    )
    surfaces = collect_surfaces([first, second])
    assert tuple(agent.name for agent in surfaces.agents) == ("agent-a", "agent-b")


def test_plugin_is_frozen(tmp_path: Path) -> None:
    plugin = load_plugin(write_plugin(tmp_path, "immutable"))
    assert isinstance(plugin, Plugin)
    with pytest.raises((AttributeError, TypeError)):
        plugin.name = "changed"  # type: ignore[misc]
