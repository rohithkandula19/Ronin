"""Plugins: one manifest that carries every other surface, installed as a directory.

A plugin is the answer to "how do I ship a skill, an MCP server, a subagent, a
command and a hook as *one* thing a teammate installs in a single step". Each of
those surfaces already loads from a checked-in directory on its own — that work is
done, and this module does not redo it. A plugin is a **directory with a manifest**
that says "these sibling paths are mine", and every method here is a thin adapter
that hands one of those paths to the loader that already owns it:

* ``skills/``     → :mod:`ronin.ext.skills` (as a :class:`SkillSource`)
* ``.mcp.json``   → :func:`ronin.mcp.config.parse_mcp_config`
* ``agents/``     → :func:`ronin.agents.definitions.load_directory`
* ``commands/``   → :func:`ronin.ui.commands.load_user_commands`
* ``hooks.json``  → :func:`ronin.agents.hooks.load_hook_config`
* ``assets/``     → opaque; the directory path is recorded, nothing is parsed.

Reusing those loaders rather than re-parsing is not only less code: it means a
plugin's ``hooks.json`` is validated by *exactly* the parser a workspace's
``hooks.json`` is, so "it worked when I dropped it in ``.ronin/``" and "it worked
inside a plugin" can never diverge. There is one grammar per surface for the whole
tree.

THE MANIFEST lives at ``<plugin>/.ronin-plugin/plugin.json`` — or, for a bundle
authored against the sibling project, ``<plugin>/.codex-plugin/plugin.json``; the
first that exists wins. It is JSON (hand-edited config deserves stdlib ``json`` and
a message that names the file, not a YAML dependency). ``name`` is required and is
an identifier because it is what ``~/.ronin/plugins/<name>/`` is keyed on and what a
skill's ``plugin:<name>`` label reads; ``version``, ``description`` and ``trust``
are optional. Unknown keys are ignored so a manifest written for a newer Ronin still
loads on an older one — but a *missing or malformed* ``name`` is a load error,
because a nameless plugin cannot be installed, shadowed, or referred to.

TRUST is recorded, not enforced here. ``official`` / ``trusted`` / ``community``
(default ``community``) is the axis a caller consults before letting a stranger's
bundle fire its shell hooks — but this layer only reads files, so it stamps the
tier and stops. Enforcement is the session's job, the same way ``Skill.source`` is
"a display string, not a trust level" one layer down.

ERRORS ARE VALUES. A malformed bundled surface does not crash a load: the surface
method raises :class:`PluginError`, and :func:`collect_surfaces` — the one function
the workspace loader calls — turns each into a note and keeps the other surfaces. A
plugin with a broken ``hooks.json`` still contributes its skills, its agent and its
command; the user is *told* the hooks were skipped rather than left wondering why a
rule stopped firing. This is the skills loader's contract exactly (one bad
``SKILL.md`` is one note, not a dead workspace), applied to a whole bundle.

Like :mod:`ronin.ext.skills`, this module reads files and constructs no live
handles: it parses configs but never connects to an MCP server, spawns a hook, or
speaks to a model. Every test runs on a ``tmp_path`` with no session at all.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agents.definitions import AgentDefinition, load_directory
from ..agents.hooks import HookConfig, HookConfigError, HookSpec
from ..mcp.config import McpServerConfig, parse_mcp_config
from ..mcp.jsonrpc import McpError
from ..ui.commands import UserCommand, load_user_commands
from .skills import SkillSource

#: The manifest directories, tried in order. ``.ronin-plugin`` is native; the
#: ``.codex-plugin`` alias lets a bundle authored against the sibling project install
#: unchanged, which is cheaper than asking every author to rename a directory.
PLUGIN_MANIFEST_DIRS: tuple[str, ...] = (".ronin-plugin", ".codex-plugin")

#: The manifest file inside one of the directories above.
MANIFEST_FILENAME = "plugin.json"

#: Where installed plugins live, relative to the home layer: ``~/.ronin/plugins/<name>``.
#: The home layer, not the project layer, because a plugin the user installed follows
#: them across every repository — the same reasoning that puts user skills under home.
PLUGINS_SUBDIR = Path(".ronin") / "plugins"

#: The marketplace listing filename, by convention. :func:`load_marketplace` takes the
#: path directly, so this is documentation and a default, never a hard-coded location.
MARKETPLACE_FILENAME = "marketplace.json"

#: The trust tiers, most-trusted first. A value outside this set is a load error rather
#: than a silent downgrade: an operator who wrote ``trust: "trused"`` meant something,
#: and treating the typo as ``community`` would grant the bundle less than they intended
#: without saying so.
TRUST_LEVELS: tuple[str, ...] = ("official", "trusted", "community")

#: What a manifest that omits ``trust`` is assumed to be. The safe end of the scale:
#: an unattributed bundle is treated as if it came from a stranger.
DEFAULT_TRUST = "community"

# The sibling paths a plugin's surfaces live at, relative to the plugin directory.
# Named constants so the manifest's contract with each loader has one home.
SKILLS_DIRNAME = "skills"
MCP_FILENAME = ".mcp.json"
AGENTS_DIRNAME = "agents"
COMMANDS_DIRNAME = "commands"
HOOKS_FILENAME = "hooks.json"
ASSETS_DIRNAME = "assets"

#: A plugin name is an identifier, the same shape a skill, command and subagent name
#: take: it is a directory name under ``~/.ronin/plugins`` and half of a
#: ``plugin:<name>`` label, so a space or a slash in it would break a path.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class PluginError(ValueError):
    """A plugin (or one of its surfaces) that could not be loaded.

    A ``ValueError`` so a caller's ``except (PluginError, OSError)`` — or the broader
    ``except (ValueError, OSError)`` the workspace loaders already use — catches it and
    turns one bad plugin into a note, exactly as :class:`ronin.ext.skills.SkillError`
    does for a bad skill.
    """


# --------------------------------------------------------------------------- #
# The plugin, and its surfaces (collected lazily, on demand)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Plugin:
    """One installed (or on-disk) plugin: its identity, and where its surfaces are.

    The surface methods are **lazy** — each looks at the filesystem only when called,
    and returns empty/``None`` when its path is absent — so a plugin that ships only a
    skill costs nothing for the four surfaces it does not have. Each may raise
    :class:`PluginError` when its path *is* present but malformed; :func:`collect_surfaces`
    is the caller that turns that into a note, so a broken surface never sinks the rest.
    """

    name: str
    directory: Path
    version: str = ""
    description: str = ""
    trust: str = DEFAULT_TRUST

    def skill_source(self) -> SkillSource | None:
        """This plugin's ``skills/`` as a :class:`SkillSource`, or ``None`` if absent.

        A source rather than a loaded ``SkillSet``: skills resolve by *precedence*
        against the user's and the project's, and only :func:`ronin.ext.skills.load_skills`
        knows that order. So the plugin contributes a lowest-tier source and lets the
        one loader that owns precedence do the merge — which is why it is labelled
        ``plugin:<name>``, the exact string a shadowing note prints.
        """
        root = self.directory / SKILLS_DIRNAME
        if not root.is_dir():
            return None
        return SkillSource(root=root, label=f"plugin:{self.name}")

    def mcp_servers(
        self, *, environ: Mapping[str, str] | None = None
    ) -> tuple[McpServerConfig, ...]:
        """Servers declared in ``<plugin>/.mcp.json``. Empty when the file is absent.

        The plugin's config lives at ``<plugin>/.mcp.json`` — a bare file, not under a
        ``.ronin/`` — so :func:`ronin.mcp.config.load_mcp_config` (which reads
        ``<root>/.ronin/mcp.json``) is the wrong entry point. The lower-level
        :func:`~ronin.mcp.config.parse_mcp_config`, which validates an already-parsed
        mapping, is exactly right: this reads and decodes the file, then hands the
        mapping over, so the plugin's servers are validated by the same rules a
        workspace's are. Nothing here connects — configs only.
        """
        path = self.directory / MCP_FILENAME
        if not path.is_file():
            return ()
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PluginError(
                f"plugin {self.name!r}: {MCP_FILENAME} could not be read: {exc}"
            ) from exc
        if not isinstance(data, Mapping):
            raise PluginError(f"plugin {self.name!r}: {MCP_FILENAME} must be a JSON object")
        try:
            return parse_mcp_config(data, environ=environ)
        # `McpError` for a config the parser rejects, `ValueError` for the invariants
        # `McpServerConfig.__post_init__` checks and does not re-wrap. `McpError` is not
        # a `ValueError`, so both must be named.
        except (McpError, ValueError) as exc:
            raise PluginError(f"plugin {self.name!r}: {MCP_FILENAME} is invalid: {exc}") from exc

    def agents(self) -> tuple[AgentDefinition, ...]:
        """Subagent definitions from ``<plugin>/agents/*.md``. Empty when absent.

        Reuses :func:`ronin.agents.definitions.load_directory`, which parses every
        ``*.md`` with the shared frontmatter grammar and raises a file-and-line error on
        a malformed one — re-raised here as :class:`PluginError` so the plugin, not just
        the file, is named.
        """
        root = self.directory / AGENTS_DIRNAME
        if not root.is_dir():
            return ()
        try:
            return load_directory(root)
        # `FrontmatterError` is a `ValueError`; a non-UTF-8 file raises `UnicodeDecodeError`
        # (also a `ValueError`) or `OSError`.
        except (ValueError, OSError) as exc:
            raise PluginError(
                f"plugin {self.name!r}: an agent in {AGENTS_DIRNAME}/ is invalid: {exc}"
            ) from exc

    def commands(self) -> tuple[UserCommand, ...]:
        """User commands from ``<plugin>/commands/*.md``. Empty when absent.

        Reuses :func:`ronin.ui.commands.load_user_commands`, which reads a commands
        directory directly and skips a badly *named* file on its own; the only failure
        that reaches here is an unreadable file, re-raised as :class:`PluginError`.
        """
        root = self.directory / COMMANDS_DIRNAME
        if not root.is_dir():
            return ()
        try:
            return load_user_commands(root)
        except OSError as exc:
            raise PluginError(
                f"plugin {self.name!r}: {COMMANDS_DIRNAME}/ could not be read: {exc}"
            ) from exc

    def hooks(self) -> HookConfig:
        """Hooks from ``<plugin>/hooks.json``. An empty :class:`HookConfig` when absent.

        Parsed through :meth:`~ronin.agents.hooks.HookConfig.normalize`, not the
        workspace loader, so a plugin may ship either dialect — Ronin's nested config or
        the flat entry list a codex/OpenAI-style bundle uses — and both register the
        same. A malformed file is re-raised as :class:`PluginError`.
        """
        path = self.directory / HOOKS_FILENAME
        if not path.is_file():
            return HookConfig()
        try:
            return HookConfig.normalize(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            raise PluginError(f"plugin {self.name!r}: {HOOKS_FILENAME} is invalid: {exc}") from exc

    def assets_dir(self) -> Path | None:
        """The ``<plugin>/assets/`` directory, or ``None``. Never parsed.

        Assets are opaque by design — a font, a template, a binary a hook shells out to
        — so recording the directory is the whole contribution. What lives in it is the
        plugin author's business and this layer takes no view on it.
        """
        root = self.directory / ASSETS_DIRNAME
        return root if root.is_dir() else None


def _read_manifest(directory: Path) -> tuple[Mapping[str, Any], Path]:
    """The first manifest that exists under ``directory``, parsed, with its path.

    Tries :data:`PLUGIN_MANIFEST_DIRS` in order. Raises :class:`PluginError` when none
    exists, when the file cannot be read, or when it is not a JSON object.
    """
    for subdir in PLUGIN_MANIFEST_DIRS:
        candidate = directory / subdir / MANIFEST_FILENAME
        if not candidate.is_file():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise PluginError(f"{candidate} could not be read: {exc}") from exc
        try:
            data: Any = json.loads(raw)
        except ValueError as exc:
            raise PluginError(f"{candidate} is not valid JSON: {exc}") from exc
        if not isinstance(data, Mapping):
            raise PluginError(f"{candidate}: the manifest must be a JSON object")
        return data, candidate
    tried = " or ".join(f"{sub}/{MANIFEST_FILENAME}" for sub in PLUGIN_MANIFEST_DIRS)
    raise PluginError(f"{directory} is not a plugin: no manifest at {tried}")


def _manifest_str(data: Mapping[str, Any], key: str, source: Path) -> str:
    """An optional string field, defaulting to ``""``. A non-string is a load error."""
    value = data.get(key, "")
    if not isinstance(value, str):
        raise PluginError(f"{source}: {key!r} must be a string, got {type(value).__name__}")
    return value


def load_plugin(directory: Path) -> Plugin:
    """Read ``directory``'s manifest into a :class:`Plugin`, or raise :class:`PluginError`.

    Validates the manifest only — the surfaces are checked when collected, not here, so
    a plugin with a broken ``hooks.json`` still loads and still contributes everything
    else. A missing manifest, a bad ``name``, or an unknown ``trust`` tier is fatal,
    because each is a thing the plugin cannot be used *at all* without.
    """
    data, source = _read_manifest(directory)

    raw_name = data.get("name")
    if not isinstance(raw_name, str) or not _NAME_RE.match(raw_name):
        raise PluginError(
            f"{source}: 'name' is required and must be lowercase letters, digits, '-' or "
            f"'_' (not starting with '-' or '_'); got {raw_name!r}"
        )

    trust = data.get("trust", DEFAULT_TRUST)
    if not isinstance(trust, str) or trust not in TRUST_LEVELS:
        raise PluginError(f"{source}: 'trust' must be one of {list(TRUST_LEVELS)}; got {trust!r}")

    return Plugin(
        name=raw_name,
        directory=directory,
        version=_manifest_str(data, "version", source),
        description=_manifest_str(data, "description", source),
        trust=trust,
    )


def load_installed(home: Path) -> tuple[tuple[Plugin, ...], tuple[str, ...]]:
    """Every plugin under ``home/.ronin/plugins``, with a note per one that was skipped.

    A missing ``plugins`` directory is the common case (most homes have none) and yields
    ``((), ())`` — not an error. A subdirectory that is not a plugin, or is a malformed
    one, becomes a note and is skipped, so one broken install does not hide the rest: a
    plugin silently missing is a capability the user believes they installed and has not.
    """
    root = home / PLUGINS_SUBDIR
    if not root.is_dir():
        return (), ()
    plugins: list[Plugin] = []
    notes: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            plugins.append(load_plugin(child))
        except (PluginError, OSError) as exc:
            notes.append(f"plugin at {child} was skipped: {exc}")
    return tuple(plugins), tuple(notes)


# --------------------------------------------------------------------------- #
# Trust: what a bundle from a stranger may do, shown before it may do it
# --------------------------------------------------------------------------- #

#: The trust tiers, most-trusted first. ``builtin`` is Ronin's own shipped surfaces
#: (not a plugin ``trust`` value — it exists here to place the tiers on one scale);
#: ``official`` is first-party, ``trusted`` is a tier an operator marked by hand, and
#: ``community`` is whatever a stranger published. Lower rank = more trusted.
TRUST_RANK: Mapping[str, int] = {"builtin": 0, "official": 1, "trusted": 2, "community": 3}


def needs_consent(trust: str) -> bool:
    """Whether enabling a plugin at this tier requires a human to say yes first.

    ``community`` does, and that is the case the spec names: a bundle from a stranger
    carries shell hooks and can add tools, so a person has to see what it wants before it
    runs. ``official``/``trusted`` are pre-vetted — by Ronin or by the operator who set
    the tier — so they enable without a prompt. An unknown tier is treated as the least
    trusted, because "I don't recognise this" is not a reason to skip the question.
    """
    return TRUST_RANK.get(trust, TRUST_RANK["community"]) >= TRUST_RANK["community"]


@dataclass(frozen=True, slots=True)
class PluginConsent:
    """What a plugin wants, gathered so a human can read it before enabling it.

    The security boundary in one value. ``shell_commands`` is the part that matters:
    a hook is a ``/bin/sh -c`` that fires on the model's actions, so a community plugin's
    hooks are arbitrary code execution the moment they are enabled. The other lists are
    the rest of the blast radius — MCP servers it will launch, subagents and commands it
    adds. :func:`inspect_for_consent` builds this without running anything.
    """

    name: str
    trust: str
    shell_commands: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    skills: bool = False

    @property
    def needs_consent(self) -> bool:
        return needs_consent(self.trust)

    def render(self) -> str:
        """The prompt a human reads before enabling this plugin. Shell hooks first."""
        lines = [f"plugin {self.name!r} (trust: {self.trust}) wants to:"]
        if self.shell_commands:
            lines.append("  RUN SHELL on your tool calls (hooks):")
            lines += [f"    $ {command}" for command in self.shell_commands]
        if self.mcp_servers:
            lines.append(f"  launch MCP servers: {', '.join(self.mcp_servers)}")
        if self.agents:
            lines.append(f"  add subagents: {', '.join(self.agents)}")
        if self.commands:
            lines.append(f"  add slash commands: {', '.join(self.commands)}")
        if self.skills:
            lines.append("  add skills (loaded on demand)")
        if len(lines) == 1:
            lines.append("  nothing but its manifest — no hooks, tools, or servers")
        return "\n".join(lines)


def inspect_for_consent(plugin: Plugin) -> PluginConsent:
    """Gather everything ``plugin`` would bring, for a consent prompt. Runs nothing.

    Malformed surfaces are swallowed here — an unreadable hooks file cannot be shown, and
    the point of this function is to *show* — so what a human approves is exactly the set
    that will actually load. A surface that fails to parse will also fail to register in
    :func:`collect_surfaces`, where it becomes a note.
    """
    shell: tuple[str, ...] = ()
    mcp: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    with suppress(PluginError, HookConfigError, ValueError, OSError):
        shell = tuple(spec.command for spec in plugin.hooks().hooks)
    with suppress(PluginError, McpError, ValueError, OSError):
        mcp = tuple(config.name for config in plugin.mcp_servers())
    with suppress(PluginError, ValueError, OSError):
        agents = tuple(definition.name for definition in plugin.agents())
    with suppress(PluginError, ValueError, OSError):
        commands = tuple(command.command.name for command in plugin.commands())
    return PluginConsent(
        name=plugin.name,
        trust=plugin.trust,
        shell_commands=shell,
        mcp_servers=mcp,
        agents=agents,
        commands=commands,
        skills=plugin.skill_source() is not None,
    )


#: A human's yes/no to a :class:`PluginConsent`. The CLI supplies a real prompt; a
#: headless caller supplies a policy. ``None`` at the ``install`` seam means "do not
#: gate" — a programmatic install whose caller owns the trust decision.
ConsentAsker = Callable[[PluginConsent], bool]


# --------------------------------------------------------------------------- #
# Installing from a local source
# --------------------------------------------------------------------------- #


def install(
    source: Path, home: Path, *, replace: bool = True, approve: ConsentAsker | None = None
) -> Plugin:
    """Copy the plugin at ``source`` into ``home/.ronin/plugins/<name>`` and return it.

    This is ``ronin plugin add <path>`` for a **local** source. ``source`` is validated
    by loading it first (so a directory that is not a plugin fails before anything is
    copied and its name is known), the tree is copied with :func:`shutil.copytree`, and
    the *installed* copy is loaded and returned so its ``directory`` points where the
    plugin now lives.

    ``approve`` is the trust gate. When the plugin :func:`needs_consent` (a community
    bundle) and an ``approve`` callback is given, it is shown the
    :class:`PluginConsent` — the shell hooks and tools it wants — **before anything is
    copied**, and a ``False`` return aborts the install with nothing written. ``None``
    (the default) does not gate: a programmatic caller owns that decision, and the
    interactive ``ronin plugin add`` always passes a real prompt. An ``official`` or
    ``trusted`` plugin is never gated — that is what the tier buys.

    A git URL is out of scope: an offline install cannot clone. The shape is built for
    the caller who can, though — clone to a temporary directory, then call this with
    that path — so the copy-and-validate half lives in one place for both.

    ``replace`` (default ``True``) overwrites an existing install of the same name; the
    reinstall-to-upgrade path is the common one. ``replace=False`` makes a name clash a
    :class:`PluginError` instead, for a caller that wants to refuse rather than clobber.
    """
    plugin = load_plugin(source)
    if (
        approve is not None
        and needs_consent(plugin.trust)
        and not approve(inspect_for_consent(plugin))
    ):
        raise PluginError(
            f"install of community plugin {plugin.name!r} was declined; nothing was written"
        )
    destination = home / PLUGINS_SUBDIR / plugin.name
    if destination.exists():
        if not replace:
            raise PluginError(
                f"a plugin named {plugin.name!r} is already installed at {destination}; "
                "pass replace=True to overwrite it"
            )
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return load_plugin(destination)


# --------------------------------------------------------------------------- #
# Marketplace: a listing of installable plugins
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MarketplaceEntry:
    """One row of a marketplace listing: a name, where to get it, and a blurb."""

    name: str
    source: str
    description: str = ""

    @property
    def is_url(self) -> bool:
        """Whether ``source`` is a URL (a remote clone) rather than a local path.

        A crude ``"://"`` test on purpose: this layer only *installs* local sources, so
        the only decision it needs is "can I resolve this to a path on disk", and a
        precise URL parser would be answering a question nobody here asks.
        """
        return "://" in self.source


@dataclass(frozen=True, slots=True)
class Marketplace:
    """A parsed ``marketplace.json`` and the directory it was read from.

    The directory is kept because an entry's ``source`` is usually a path *relative to
    the listing* — a marketplace is a folder of plugins with an index at its root — and
    :meth:`local_source` needs the anchor to resolve against.
    """

    directory: Path
    entries: tuple[MarketplaceEntry, ...] = ()

    def get(self, name: str) -> MarketplaceEntry | None:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def local_source(self, entry: MarketplaceEntry) -> Path:
        """The on-disk path ``entry`` resolves to, for handing to :func:`install`.

        A relative ``source`` resolves against :attr:`directory`; an absolute one is
        taken as-is. A URL raises :class:`PluginError` — cloning it is the caller's job,
        after which they install from the local checkout.
        """
        if entry.is_url:
            raise PluginError(
                f"marketplace entry {entry.name!r} has a URL source ({entry.source}); "
                "clone it to a local path first, then install that path"
            )
        return self.directory / entry.source


def load_marketplace(path: Path) -> Marketplace:
    """Parse a ``marketplace.json`` listing. Every rejection names the file and the row.

    Shape::

        {"plugins": [{"name": ..., "source": <path or url>, "description": ...}, ...]}

    ``name`` and ``source`` are required per entry; ``description`` is optional. Unknown
    keys are ignored, the same forward-compatibility the manifest gives.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginError(f"{path} could not be read: {exc}") from exc
    try:
        data: Any = json.loads(raw)
    except ValueError as exc:
        raise PluginError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PluginError(f"{path}: a marketplace must be a JSON object")

    rows = data.get("plugins", [])
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise PluginError(f"{path}: 'plugins' must be a list of entries")

    entries: list[MarketplaceEntry] = []
    for index, row in enumerate(rows):
        where = f"{path}: plugins[{index}]"
        if not isinstance(row, Mapping):
            raise PluginError(f"{where} must be an object")
        name = row.get("name")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise PluginError(
                f"{where}: 'name' is required and must be an identifier; got {name!r}"
            )
        raw_source = row.get("source")
        if not isinstance(raw_source, str) or not raw_source.strip():
            raise PluginError(f"{where}: 'source' is required and must be a non-empty string")
        description = row.get("description", "")
        if not isinstance(description, str):
            raise PluginError(f"{where}: 'description' must be a string")
        entries.append(MarketplaceEntry(name=name, source=raw_source, description=description))
    return Marketplace(directory=path.parent, entries=tuple(entries))


# --------------------------------------------------------------------------- #
# The aggregate: every surface a set of plugins contributes, in one value
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PluginSurfaces:
    """Everything a set of plugins adds to a workspace, flattened and ready to merge.

    One value so the workspace loader has a single thing to fold in rather than five
    parallel lists it has to keep in step. ``skill_sources`` stays as sources (not a
    merged ``SkillSet``) because precedence against the user's and project's skills is
    :func:`ronin.ext.skills.load_skills`'s to decide, not this layer's — see
    :meth:`Plugin.skill_source`. ``notes`` names every surface that was skipped.
    """

    skill_sources: tuple[SkillSource, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()
    agents: tuple[AgentDefinition, ...] = ()
    commands: tuple[UserCommand, ...] = ()
    hooks: tuple[HookSpec, ...] = ()
    notes: tuple[str, ...] = ()


def collect_surfaces(
    plugins: Sequence[Plugin], *, environ: Mapping[str, str] | None = None
) -> PluginSurfaces:
    """Gather every plugin's surfaces into one :class:`PluginSurfaces`.

    The single function the workspace loader calls. Each surface is collected inside its
    own guard, so a malformed one becomes a note and the others still register — a
    plugin with a broken ``hooks.json`` still contributes its skills, its servers, its
    agents and its commands. Order is preserved: surfaces come out in the order the
    plugins were given, which is the order the caller chose to resolve precedence in.

    ``environ`` is threaded to MCP config parsing for ``${VAR}`` expansion in a plugin's
    ``.mcp.json``; it defaults to nothing, the same fail-closed choice the workspace MCP
    loader makes (an unset variable is a named error, not an empty token).
    """
    skill_sources: list[SkillSource] = []
    mcp_servers: list[McpServerConfig] = []
    agents: list[AgentDefinition] = []
    commands: list[UserCommand] = []
    hook_specs: list[HookSpec] = []
    notes: list[str] = []

    for plugin in plugins:
        # Skills carry no parse here (a SkillSource just points at a directory), so this
        # cannot fail — a malformed SKILL.md surfaces later, as a note out of load_skills.
        source = plugin.skill_source()
        if source is not None:
            skill_sources.append(source)

        # Each surface is guarded on its own so one malformed bundle file (a bad
        # .mcp.json, say) costs only that surface and the note that explains it — the
        # plugin's other four surfaces still register.
        try:
            mcp_servers.extend(plugin.mcp_servers(environ=environ))
        except PluginError as exc:
            notes.append(str(exc))
        try:
            agents.extend(plugin.agents())
        except PluginError as exc:
            notes.append(str(exc))
        try:
            commands.extend(plugin.commands())
        except PluginError as exc:
            notes.append(str(exc))
        try:
            hook_specs.extend(plugin.hooks().hooks)
        except PluginError as exc:
            notes.append(str(exc))

    return PluginSurfaces(
        skill_sources=tuple(skill_sources),
        mcp_servers=tuple(mcp_servers),
        agents=tuple(agents),
        commands=tuple(commands),
        hooks=tuple(hook_specs),
        notes=tuple(notes),
    )


__all__ = [
    "AGENTS_DIRNAME",
    "ASSETS_DIRNAME",
    "COMMANDS_DIRNAME",
    "DEFAULT_TRUST",
    "HOOKS_FILENAME",
    "MANIFEST_FILENAME",
    "MARKETPLACE_FILENAME",
    "MCP_FILENAME",
    "PLUGINS_SUBDIR",
    "PLUGIN_MANIFEST_DIRS",
    "SKILLS_DIRNAME",
    "TRUST_LEVELS",
    "TRUST_RANK",
    "ConsentAsker",
    "Marketplace",
    "MarketplaceEntry",
    "Plugin",
    "PluginConsent",
    "PluginError",
    "PluginSurfaces",
    "collect_surfaces",
    "inspect_for_consent",
    "install",
    "load_installed",
    "load_marketplace",
    "load_plugin",
    "needs_consent",
]
