"""Reading a workspace, then assembling a session out of it. The joins live here.

Two functions, and the split between them is the whole design:

:func:`load_workspace` **reads**. It opens files and returns a
:class:`~ronin.cli.spine.Loaded`, and it may not open a socket, spawn a subprocess or
construct a model client. That restriction is what makes ``/doctor`` and the first-run
wizard possible: both want to report on a workspace, and neither should have to start
a session to do it.

:func:`build_runtime` **wires**. Every object below ``cli`` was built against an
injected protocol so it could be tested alone, which means no test below this module
can catch a *missing* join — a `TaintTracker` nobody constructs from the configured
``taint_min_span`` escalates nothing and no unit test notices, because the tracker's
own tests construct it by hand. Four of those joins are made here and nowhere else:

* ``Settings.taint_min_span`` → the :class:`~ronin.safety.injection.TaintTracker` the
  policy engine consults;
* ``Settings.sandbox`` → :func:`ronin.safety.sandbox.detect` → ``PolicyEngine.sandbox``;
* :class:`~ronin.verify.checkpoints.CheckpointStore` → ``Denylist.has_checkpoint``, so
  "``git reset --hard`` with nothing to restore from" is judged against reality rather
  than against the honest-but-pessimistic default;
* ``Settings`` → ``settings.local.json``, so a remembered rule is written to the
  gitignored layer and never to the layer a team shares;
* the parent's rules → the *subagent's* policy, so a child can act on permission the
  user already granted without being able to ask for more (see
  :func:`subagent_policy_factory`).

**Every degradation is a note, never a log line.** A workspace with an unparseable
``settings.local.json``, no tree-sitter, no git and no ``RONIN.md`` still loads and
still runs; it just does less, and the user has to be told exactly what less means.
:func:`ronin.cli.spine.notes_from_settings`, :func:`~ronin.cli.spine.sandbox_note` and
:func:`~ronin.cli.spine.sequence_note` turn the three commonest shapes into notes.

One thing this module deliberately does **not** do: parse verify commands out of
``RONIN.md``. :func:`ronin.verify.spec.detect_verify_spec` takes a ``declared``
mapping and ``ronin.context.memory`` produces no such mapping from a RONIN.md — the
nearest thing, ``discover_commands``, re-reads ``package.json`` / ``pyproject.toml`` /
``Makefile`` / CI, which is the same evidence ``detect_verify_spec`` already reads for
itself. Feeding it in as ``declared`` would relabel an inferred command as "declared
explicitly in RONIN.md" and destroy the provenance both modules are built around, so
``declared`` is left unset and the gap is reported as a note instead.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agents.definitions import (
    BUILTIN_AGENTS,
    AgentDefinition,
    load_agents,
    subagent_catalogue,
)
from ..agents.hooks import HookConfig, HookRunner, load_hook_config
from ..context.budget import OutputBudget
from ..context.filestate import FileStateTracker
from ..context.memory import Memory, load_memory
from ..context.repomap import (
    DEFAULT_BUDGET_TOKENS,
    Parser,
    RepoMap,
    build_repo_map,
)
from ..core.protocols import Policy
from ..core.types import Budget
from ..ext.plugins import PluginSurfaces, collect_surfaces, load_installed
from ..ext.skills import BUILTIN_SKILLS_DIR, SkillSet, SkillSource, SkillTool, load_skills
from ..mcp.client import (
    TokenRefresher,
    TransportProvider,
    connect_all,
    default_transport_provider,
)
from ..mcp.config import AuthKind, McpServerConfig, TransportKind, load_mcp_config
from ..mcp.jsonrpc import McpError
from ..mcp.oauth_driver import (
    OAuthDriver,
    authorize_servers,
    default_oauth_driver,
    token_refresher,
)
from ..mcp.tools import extend_registry
from ..mcp.transport import HttpxSender
from ..persistence.index import SessionIndex
from ..persistence.transcript import Transcript, new_session_id
from ..providers.accounting import Ledger
from ..providers.router import Role as ModelRole
from ..providers.router import Router
from ..safety.denylist import Denylist
from ..safety.injection import TaintTracker
from ..safety.policy import (
    Asker,
    CommandRegex,
    Exact,
    PathGlob,
    PolicyEngine,
    Rule,
    UnattendedAsker,
)
from ..safety.sandbox import NoSandbox, Sandbox, Unavailable, detect
from ..safety.settings import Settings, load_settings
from ..session import SubagentPolicyFactory, build_session
from ..tools.base import MAX_RESULT_CHARS, Tool, ToolContext
from ..tools.fetcher import pinned_fetcher
from ..tools.net import Searcher
from ..tools.registry import ToolRegistry, build_registry
from ..tools.searcher import SearchError, provider_named, provider_searcher
from ..tools.shell import PersistentShell, ShellSession
from ..ui.commands import Registry as CommandRegistry
from ..ui.commands import load_registry
from ..verify.checkpoints import CheckpointStore
from ..verify.spec import VerifySpec, detect_verify_spec
from .gate import gated
from .spine import (
    Closer,
    Loaded,
    Note,
    Paths,
    Runtime,
    notes_from_settings,
    sandbox_note,
    sequence_note,
)
from .stream import extractor_for

#: Context window assumed when the caller does not say. Deliberately smaller than the
#: largest window any shipped provider offers: compacting earlier than necessary costs
#: one summarization, while assuming a window the model does not have costs the turn.
#: The real value belongs in the router config; this is the floor a bare run gets.
LEDGER_FILENAME = "ledger.db"

DEFAULT_CONTEXT_WINDOW = 128_000

#: Ceiling handed to the gate's :class:`~ronin.context.budget.OutputBudget`. The same
#: number the tool layer caps a single result at, so the gate and the tools cannot
#: disagree about how much output is too much.
DEFAULT_OUTPUT_BUDGET_CHARS = MAX_RESULT_CHARS

#: The system text a session starts from, before ``Loaded.system_suffix()`` appends
#: RONIN.md and the repo map. Nothing in the tree shipped one, so this is written here;
#: it is short on purpose — the tool descriptions already carry per-tool guidance, and
#: a long system prompt competes with the user's code for the same window.
BASE_SYSTEM_PROMPT = """\
You are ronin, a coding agent working in a real repository on the user's machine.

Work like an engineer who has to live with the result:

- Read before you write. Open the file you are about to change, and the code that
  calls it, before proposing an edit.
- Make the smallest change that does the job, in the style of the code around it.
  A patch that does not look like its neighbours will not be maintained.
- Verify. After a change, run the project's own test/lint/typecheck commands and
  report what they said. "It should work" is not a result.
- When something is missing — a command, a file, a permission — say so plainly and
  name what you would need. Do not invent a plausible substitute.
- Never claim you ran something you did not run, and never report a number you did
  not observe.

The user sees your final message and nothing else, so it has to stand alone: what
you changed, which files, what you verified, and anything you left undone.
"""


# --------------------------------------------------------------------------- #
# Reading the workspace
# --------------------------------------------------------------------------- #


def load_workspace(
    paths: Paths,
    *,
    flags: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    parser: Parser | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform: str = sys.platform,
    repo_map_budget: int = DEFAULT_BUDGET_TOKENS,
) -> Loaded:
    """Read everything one run needs off disk. Never raises for a workspace's sake.

    Nine loaders run, and each of them can fail without the session being unusable —
    so each failure becomes a :class:`~ronin.cli.spine.Note` and the rest still load.
    An empty directory is a legitimate workspace and produces a ``Loaded`` that says
    what it has not got.

    ``environ`` is the mapping ``${VAR}`` references in ``.ronin/mcp.json`` expand
    against, and it defaults to *nothing* rather than to ``os.environ``: a process
    environment read from inside a library is a hidden dependency, and an unset
    variable here is a named config error rather than an empty token sent to a server.
    """
    settings = load_settings(
        home=paths.home,
        # The workspace root, not `paths.cwd`. `load_settings` derives the project and
        # local layer paths from this argument, and `Paths.project_settings` derives
        # them from `workspace_root` — passing cwd would make /doctor print one path
        # while the loader read another.
        cwd=paths.workspace_root,
        flags=flags,
    )
    notes: list[Note] = list(notes_from_settings(settings))

    if not (paths.workspace_root / ".git").exists():
        notes.append(
            Note(
                subject="git",
                detail=(
                    f"{paths.workspace_root} is not a git repository, so checkpoints "
                    "are off (/undo and /diff will not work) and the repo map has no "
                    ".gitignore to respect. run `git init` to enable them"
                ),
            )
        )

    memory, memory_note = _load_memory(paths)
    if memory_note is not None:
        notes.append(memory_note)

    repo_map, map_notes = _load_repo_map(paths, parser=parser, budget=repo_map_budget)
    notes.extend(map_notes)

    agents, agents_note = _load_agents(paths)
    if agents_note is not None:
        notes.append(agents_note)

    hooks, hooks_note = _load_hooks(paths)
    if hooks_note is not None:
        notes.append(hooks_note)

    verify, verify_notes = _load_verify(paths, memory)
    notes.extend(verify_notes)

    servers, mcp_note = _load_mcp(paths, environ=environ)
    if mcp_note is not None:
        notes.append(mcp_note)

    commands, commands_note = _load_commands(paths)
    if commands_note is not None:
        notes.append(commands_note)

    # Installed plugins are the lowest tier of every surface. Loaded before skills so
    # their `skills/` join the discovery precedence as an even lower tier than the
    # built-in role workflows; merged into the rest just below.
    surfaces, plugin_notes = _load_plugins(paths, environ)
    notes.extend(plugin_notes)

    skill_extra = (
        SkillSource(root=BUILTIN_SKILLS_DIR, label="builtin"),
        *surfaces.skill_sources,
    )
    skills, skills_notes = _load_skills(paths, extra=skill_extra)
    notes.extend(skills_notes)

    # Fold plugin-contributed surfaces in, plugins losing every collision: a name a
    # plugin defines never displaces the same name from the project or a builtin.
    agents = {**{d.name: d for d in surfaces.agents}, **dict(agents)}
    servers = _merge_servers(surfaces.mcp_servers, servers)
    commands = commands.with_user((*commands.user, *surfaces.commands))
    if surfaces.hooks:
        # Hooks are additive, not shadowing — every matching hook runs — so plugin hooks
        # are appended after the workspace's, which only sets execution order.
        hooks = HookConfig(hooks=(*hooks.hooks, *surfaces.hooks))

    sandbox = _load_sandbox(settings, which=which, platform=platform)
    note = sandbox_note(sandbox)
    if note is not None:
        notes.append(note)

    return Loaded(
        paths=paths,
        settings=settings,
        memory=memory,
        repo_map=repo_map,
        agents=agents,
        hooks=hooks,
        verify=verify,
        mcp_servers=servers,
        commands=commands,
        skills=skills,
        sandbox=sandbox,
        notes=tuple(notes),
    )


def _merge_servers(
    plugin_servers: Sequence[McpServerConfig], workspace_servers: Sequence[McpServerConfig]
) -> tuple[McpServerConfig, ...]:
    """Workspace MCP servers plus plugin ones, workspace winning a name clash.

    Names become the ``mcp__<server>__<tool>`` namespace, so two servers of one name
    cannot coexist — and a plugin must not be able to shadow a server the project
    configured. Workspace servers are kept; a plugin server whose name is already taken
    is dropped.
    """
    taken = {config.name for config in workspace_servers}
    kept = [config for config in plugin_servers if config.name not in taken]
    return (*kept, *workspace_servers)


def _load_memory(paths: Paths) -> tuple[Memory, Note | None]:
    """RONIN.md, walking up from where the user actually is.

    ``cwd`` rather than the root: a monorepo package's RONIN.md must win over the
    repo's, and that ordering is a property of where the user is standing.
    """
    memory = load_memory(paths.cwd, home=paths.home, root=paths.workspace_root)
    if not memory.is_empty:
        return memory, None
    return memory, Note(
        subject="memory",
        detail=(
            "no RONIN.md was found, so the model has no standing instructions about "
            "this repo — it will guess at the test command and the conventions. run "
            "the first-run wizard to draft one from what is already in the tree"
        ),
    )


def _load_repo_map(
    paths: Paths, *, parser: Parser | None, budget: int
) -> tuple[RepoMap, tuple[Note, ...]]:
    """The repo map, plus a note naming every file that contributed no signatures.

    The per-file note is how "tree-sitter is not installed" becomes visible: the
    stdlib Python parser always works, so a caller who asked for wider language
    coverage and did not get it would otherwise just see a map with no Rust in it.
    """
    try:
        repo_map = build_repo_map(
            paths.workspace_root,
            budget_tokens=budget,
            parser=parser,
            cache_dir=paths.cache_dir,
        )
    except (OSError, ValueError) as exc:
        return RepoMap(budget_tokens=budget), (
            Note(
                subject="repo map",
                detail=(
                    f"could not be built ({exc}); the model starts with no map of the "
                    "codebase and will have to search for everything"
                ),
            ),
        )
    failed = tuple(entry.path for entry in repo_map.entries if entry.parse_error)
    note = sequence_note(
        "repo map",
        failed,
        detail=(
            "these files contributed no signatures, so the model cannot see what they "
            "define without opening them"
        ),
    )
    return repo_map, (note,) if note is not None else ()


def _load_agents(paths: Paths) -> tuple[dict[str, AgentDefinition], Note | None]:
    """Subagent definitions, falling back to the builtins when a file is malformed.

    A malformed ``.ronin/agents/*.md`` raises out of ``load_agents`` on purpose — the
    error names the file and the line. Here it degrades to the builtins, because one
    bad file in a shared repo must not stop the session, and the note carries the
    error verbatim so it is still fixable.
    """
    try:
        return load_agents(paths.workspace_root), None
    # `ValueError` covers both arms that can actually reach here: `FrontmatterError`
    # is one, and a non-UTF-8 definition file raises `UnicodeDecodeError`, which is
    # the other. Narrowing to FrontmatterError alone would let a mojibake file take
    # the session down.
    except (ValueError, OSError) as exc:
        return dict(BUILTIN_AGENTS), Note(
            subject="subagents",
            detail=(
                f"{exc} — every file in {paths.agents_dir} was skipped and only the "
                "built-in subagents are available"
            ),
        )


def _load_hooks(paths: Paths) -> tuple[HookConfig, Note | None]:
    try:
        return load_hook_config(paths.hooks_config), None
    except (ValueError, OSError) as exc:  # HookConfigError is a ValueError
        return HookConfig(), Note(
            subject="hooks",
            detail=(
                f"{exc} — no hooks will run this session, so any check they were "
                "enforcing (formatting, a lint gate) is not being enforced"
            ),
        )


def _load_mcp(
    paths: Paths, *, environ: Mapping[str, str] | None
) -> tuple[tuple[McpServerConfig, ...], Note | None]:
    try:
        return load_mcp_config(paths.workspace_root, environ=environ), None
    # `McpError` for a config the loader rejects, `ValueError` for the handful of
    # invariants `McpServerConfig.__post_init__` checks and does not re-wrap.
    except (McpError, ValueError, OSError) as exc:
        return (), Note(
            subject="mcp config",
            detail=(
                f"{exc} — no MCP servers were loaded, so none of their tools exist this session"
            ),
        )


def _load_commands(paths: Paths) -> tuple[CommandRegistry, Note | None]:
    try:
        return load_registry(paths.workspace_root), None
    except (ValueError, OSError) as exc:
        return CommandRegistry(), Note(
            subject="slash commands",
            detail=(
                f"{paths.commands_dir} could not be read ({exc}); only the built-in "
                "commands are available"
            ),
        )


def _load_skills(
    paths: Paths, *, extra: Sequence[SkillSource] = ()
) -> tuple[SkillSet, tuple[Note, ...]]:
    """Discover skills from every tier, lowest first. Every problem is a note.

    ``extra`` is the lowest-precedence tiers — the built-in role workflows shipped inside
    Ronin, then any installed plugin's ``skills/`` — so the two on-disk tiers a user
    controls (``~/.ronin/skills`` then ``./.ronin/skills``) shadow them. A malformed or
    missing skill never stops a session: one bad ``SKILL.md`` is one workflow
    unavailable, not a dead workspace, so :func:`load_skills` returns its complaints as
    strings and they become notes here.
    """
    try:
        skillset, messages = load_skills(paths.workspace_root, paths.home, extra=extra)
    except OSError as exc:
        return SkillSet(), (
            Note(subject="skills", detail=f"{paths.skills_dir} could not be read ({exc})"),
        )
    return skillset, tuple(Note(subject="skills", detail=message) for message in messages)


def _load_plugins(
    paths: Paths, environ: Mapping[str, str] | None
) -> tuple[PluginSurfaces, list[Note]]:
    """Every installed plugin's surfaces, and a note for each one that could not load.

    Installed plugins live under ``~/.ronin/plugins``. Each contributes skills, MCP
    servers, subagents, commands and hooks, and every one of those is the **lowest**
    precedence tier — a plugin can add a ``review`` command, but a ``review`` you wrote
    in your own project still wins. A plugin that fails to load, or a surface inside one
    that is malformed, becomes a note and the rest still load: an ecosystem where one bad
    third-party bundle bricks the session is an ecosystem nobody enables.
    """
    plugins, plugin_notes = load_installed(paths.home)
    surfaces = collect_surfaces(plugins, environ=environ)
    notes = [
        Note(subject="plugins", detail=message) for message in (*plugin_notes, *surfaces.notes)
    ]
    return surfaces, notes


def _load_sandbox(
    settings: Settings, *, which: Callable[[str], str | None], platform: str
) -> Sandbox | Unavailable:
    """Detect a backend **only when the settings asked for one**.

    Sandbox mode is off by default, and probing for ``bwrap`` on a machine that never
    asked to be sandboxed would put a ``PATH`` lookup in the load path and, worse,
    make ``/doctor`` report a sandbox nobody enabled.
    """
    if not settings.sandbox:
        return NoSandbox()
    return detect(which=which, platform=platform)


def _load_verify(paths: Paths, memory: Memory) -> tuple[VerifySpec, tuple[Note, ...]]:
    """Detect this repo's verify commands. A detector that cannot read a file survives.

    ``detect_verify_spec`` reads six file formats and swallows the I/O failures itself;
    the guard is for the residue — a ``pyproject.toml`` whose declared value is a shape
    ``DetectedCommand`` rejects. Verification going quiet is survivable; a session that
    will not start because a manifest is odd is not.
    """
    try:
        spec = detect_verify_spec(paths.workspace_root)
    except (ValueError, OSError) as exc:
        return VerifySpec(), (
            Note(
                subject="verify",
                detail=(
                    f"detection failed ({exc}); /verify will report 'unknown' rather "
                    "than run anything, so check changes by hand"
                ),
            ),
        )
    note = _verify_declaration_note(memory, spec)
    return spec, (note,) if note is not None else ()


def _verify_declaration_note(memory: Memory, verify: VerifySpec) -> Note | None:
    """Name the gap between "my RONIN.md says how to test this" and detection.

    Only when the tree yielded nothing, because that is the one case where a user has
    plausibly written the command down and reasonably expects it to be used. See this
    module's docstring for why the two are not joined.
    """
    if verify.empty and not memory.is_empty:
        return Note(
            subject="verify",
            detail=(
                "no verify command could be detected from this tree, and a test "
                "command written in prose in RONIN.md is not parsed into one — the "
                "model will read it, but /verify has nothing to run. declare the "
                "command in pyproject.toml, package.json, a Makefile or a justfile "
                "to make it detectable"
            ),
        )
    return None


# --------------------------------------------------------------------------- #
# Persisting a remembered rule
# --------------------------------------------------------------------------- #


def rule_to_json(rule: Rule) -> dict[str, Any]:
    """One rule in the JSON form ``ronin.safety.settings.parse_rule`` reads back.

    Round-trippable on purpose: a rule written to disk that the loader would reject is
    a permission the user believes they granted and does not have.
    """
    matcher = rule.matcher
    match: dict[str, Any]
    if isinstance(matcher, Exact):
        match = {"kind": "exact", "argument": matcher.argument, "value": matcher.value}
    elif isinstance(matcher, PathGlob):
        match = {"kind": "path", "pattern": matcher.pattern, "argument": matcher.argument}
    elif isinstance(matcher, CommandRegex):
        match = {"kind": "regex", "pattern": matcher.pattern}
    else:
        match = {"kind": "tool"}
    body: dict[str, Any] = {
        "tool": rule.tool,
        "decision": rule.decision.value,
        "match": match,
    }
    if rule.reason:
        body["reason"] = rule.reason
    if rule.unwaivable:
        body["always_ask"] = True
    return body


@dataclass(slots=True)
class LocalRuleWriter:
    """Appends a remembered rule to ``.ronin/settings.local.json``.

    Mutable because it accumulates the writes it could not perform; that is the state
    it exists to hold.

    Two decisions worth stating:

    **The local layer, never the project layer.** ``settings.json`` is committed and
    shared; a permission one developer granted at 2am must not arrive in a colleague's
    checkout as policy. ``settings.local.json`` is gitignored, which is the whole
    reason that layer exists.

    **A file that does not parse is not overwritten.** Rewriting it would silently
    discard every rule the user had already written. The rule is dropped from *disk*
    instead — the policy engine has already added it to the session, so the approval
    still holds for this run — and the reason is recorded in :attr:`failures` rather
    than raised, because raising here would kill a turn in the middle of an approval.
    """

    path: Path
    failures: list[str] = field(default_factory=list)

    def __call__(self, rule: Rule) -> None:
        try:
            body = self._read()
        except ValueError as exc:
            self.failures.append(str(exc))
            return
        rules = body.get("rules", [])
        if not isinstance(rules, list):
            self.failures.append(
                f"{self.path} has a 'rules' key that is not a list, so the remembered "
                "rule was not written. it still applies for this session"
            )
            return
        entry = rule_to_json(rule)
        if entry in rules:
            return
        body["rules"] = [*rules, entry]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            self.failures.append(
                f"{self.path} could not be written ({exc}), so the remembered rule "
                "applies for this session only"
            )

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ValueError(
                f"{self.path} could not be read ({exc}); the remembered rule was not "
                "written and applies for this session only"
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{self.path} is not valid JSON ({exc}); it was left untouched rather "
                "than overwritten, so the rules already in it are not lost. the "
                "remembered rule applies for this session only"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"{self.path} does not contain a JSON object; it was left untouched "
                "and the remembered rule applies for this session only"
            )
        return data


# --------------------------------------------------------------------------- #
# Assembling the runtime
# --------------------------------------------------------------------------- #


async def build_runtime(
    loaded: Loaded,
    router: Router,
    *,
    session_id: str | None = None,
    asker: Asker | None = None,
    record: bool = True,
    connect_mcp: bool = True,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    shell: ShellSession | None = None,
    transport_provider: TransportProvider | None = None,
    oauth_driver: OAuthDriver | None = None,
    extra_tools: Sequence[Tool] = (),
) -> Runtime:
    """Turn a :class:`~ronin.cli.spine.Loaded` into live, wired objects.

    The ordering here is not arbitrary. ``build_registry`` is called **without**
    ``task`` and ``build_session`` adds it, because ``TaskTool`` needs a runner, the
    runner needs a registry to subset, and the registry contains ``TaskTool`` — see
    :func:`ronin.session.build_session`. Doing it the other way round is a cycle.

    The gate wraps the *session's* registry, so ``task`` and every MCP tool go through
    the same policy, hooks and output budget as ``bash``.

    Nothing about a failed MCP server or an unavailable sandbox raises: each becomes a
    note on ``Runtime.loaded``.
    """
    paths = loaded.paths
    settings = loaded.settings
    notes: list[Note] = []
    closers: list[Closer] = []

    sid = session_id or new_session_id()

    checkpoints = CheckpointStore(paths.workspace_root)
    denylist = Denylist(
        workspace_root=paths.workspace_root,
        home=paths.home,
        # The live store, not the pessimistic default: `git reset --hard` is refused
        # only while there is genuinely nothing to restore from. `session_base` is the
        # first checkpoint of this session and is the one synchronous signal the store
        # exposes, which is what `Denylist.has_checkpoint` needs.
        has_checkpoint=lambda: checkpoints.session_base is not None,
        protected_branches=settings.protected_branches,
        yolo=settings.yolo,
    )

    # The join the safety package could not make for itself: the field is parsed by
    # `load_settings` and this is the only place a tracker is constructed from it.
    taint = TaintTracker(min_span=settings.taint_min_span)

    engine_sandbox = _engine_sandbox(loaded.sandbox)
    writer = LocalRuleWriter(paths.local_settings)
    policy = PolicyEngine(
        rules=settings.ruleset(),
        asker=asker if asker is not None else UnattendedAsker(),
        denylist=denylist,
        mode=loaded.mode,
        taint=taint,
        sandbox=engine_sandbox,
        persist=writer,
    )

    ctx = ToolContext(root=paths.workspace_root)
    shell_session = (
        shell
        if shell is not None
        else ShellSession(shell=PersistentShell(cwd=paths.workspace_root, env=ctx.env))
    )
    if shell is None:
        # Only close what we opened. A caller who injected a shell owns its lifetime,
        # and closing someone else's shell kills their background jobs.
        closers.append(shell_session.close)

    # `web_fetch` exists from here on. It did not before: `Fetcher` was a type alias
    # with no implementation, so the tool was assembled only by demos and tests handing
    # in a fake, and every real session had no way to read a URL at all. The client is
    # `tools.fetcher.pinned_fetcher`, which resolves, vets every address the name
    # answers with, and connects to *those* rather than to the name — see that module
    # for why the second half is what makes the first half more than advisory.
    #
    # `web_search` appears only when a provider is configured (RONIN_SEARCH_PROVIDER).
    # Absent otherwise, deliberately: a search tool with no backend that errors on every
    # call teaches the model to keep trying it, and `build_registry` is built around
    # exactly this — every group is opt-in by supplying its dependency.
    searcher, search_note = searcher_from_env(ctx.env)
    if search_note is not None:
        notes.append(search_note)
    base_tools = build_registry(
        ctx,
        shell=shell_session,
        fetch=pinned_fetcher(),
        extract=extractor_for(router),
        search=searcher,
        clock=time.monotonic,
    )
    session = build_session(
        router,
        ctx,
        base_tools=base_tools,
        # The per-request ledger, wired at last. It was written, tested and then never
        # constructed by the CLI, so `Budget` (provider-reported totals only, no price
        # fallback, no role split, no cache-hit rate) was all `/cost` could report.
        ledger=Ledger(paths.cache_dir / LEDGER_FILENAME),
        session_id=sid,
        # Empty on purpose: the map is in `Runtime.system` via `Loaded.system_suffix`,
        # and passing it here as well would put it in the prompt twice. The cost is
        # that subagents do not receive it — see this decision in the report.
        repo_map="",
        subagent_types=subagent_catalogue(loaded.agents),
        subagent_policy=subagent_policy_factory(policy),
    )

    inner: ToolRegistry = session.registry
    extras: list[Tool] = list(extra_tools)
    if loaded.skills.skills:
        # Only when there are skills to load. A `skill` tool over an empty catalog would
        # deny every call, which is the always-refusing-tool anti-pattern the search and
        # MCP layers already avoid — a group appears exactly when its dependency does.
        extras.append(SkillTool(loaded.skills))
    if extras:
        # Folded in *here*, above the session's registry and below `gated`, so a tool an
        # embedder brought — or the skill loader — is gated exactly like a builtin: same
        # policy, same hooks, same taint tracking, same output budget. Adding it after
        # the gate would be the one arrangement worth forbidding — a caller's tool would
        # then be the only thing in the process that could write a file without asking.
        inner = ToolRegistry((*inner.tools(), *extras), ctx)
    if connect_mcp and loaded.mcp_servers:
        inner, mcp_notes, mcp_closer = await _connect_mcp(
            loaded.mcp_servers,
            inner,
            transport_provider,
            workspace_root=paths.workspace_root,
            oauth_driver=oauth_driver,
        )
        notes.extend(mcp_notes)
        closers.append(mcp_closer)

    files = FileStateTracker()
    hooks = HookRunner(config=loaded.hooks, cwd=str(paths.workspace_root), env=ctx.env)
    registry = gated(
        inner,
        policy,
        hooks=hooks,
        files=files,
        taint=taint,
        budget=OutputBudget(remaining_chars=DEFAULT_OUTPUT_BUDGET_CHARS),
    )

    transcript: Transcript | None = None
    if record:
        # The index is opened here and handed to the transcript rather than built
        # inside it, so the one place that decides a session is recorded is also the
        # one place that decides it is indexed. `SessionIndex.open` does not raise —
        # a database it cannot prepare degrades to one that records the problem — so
        # this cannot be the reason a session fails to start.
        transcript = Transcript.open(
            paths.sessions_dir,
            sid,
            model=router.spec_for(ModelRole.MAIN).model,
            cwd=str(paths.cwd),
            index=SessionIndex.open(paths.sessions_dir),
        )

    return Runtime(
        loaded=loaded.with_notes(*notes),
        session=session,
        registry=registry,
        policy=policy,
        files=files,
        taint=taint,
        hooks=hooks,
        checkpoints=checkpoints,
        compaction=loaded.compaction_policy(context_window=context_window),
        system=system_prompt(loaded),
        transcript=transcript,
        closers=tuple(closers),
    )


#: Names the search provider ``web_search`` uses. Unset means no ``web_search`` at all,
#: which is the honest default: a search tool with no backend that errors on every call
#: teaches the model to keep trying it.
SEARCH_PROVIDER_ENV = "RONIN_SEARCH_PROVIDER"

#: Overrides the provider's default endpoint. Required in practice for ``searxng``,
#: since the instance is yours and this build cannot guess where you run it.
SEARCH_ENDPOINT_ENV = "RONIN_SEARCH_ENDPOINT"


def searcher_from_env(env: Mapping[str, str]) -> tuple[Searcher | None, Note | None]:
    """``(searcher, note)`` for the configured search provider, or ``(None, …)``.

    Reading configuration is this layer's job, not the tool layer's:
    :mod:`ronin.tools.searcher` takes a provider, a key and an endpoint as arguments and
    stays pure, which is what lets its tests run with no environment and no network.

    Every failure is a :class:`~ronin.cli.spine.Note` and ``None`` rather than an
    exception — a mistyped provider name must not stop a session that has nothing to do
    with searching, and a user who set ``RONIN_SEARCH_PROVIDER`` and got no
    ``web_search`` needs to be told which of the two reasons applies.
    """
    name = env.get(SEARCH_PROVIDER_ENV, "").strip()
    if not name:
        return None, None
    try:
        provider = provider_named(name)
    except SearchError as exc:
        return None, Note(subject="web_search", detail=str(exc))

    endpoint = env.get(SEARCH_ENDPOINT_ENV, "").strip()
    key = env.get(provider.key_env, "").strip() if provider.needs_key else ""
    if provider.needs_key and not key:
        return None, Note(
            subject="web_search",
            detail=(
                f"provider {provider.name!r} is selected but {provider.key_env} is empty, "
                f"so web_search is not offered. Set it, or unset "
                f"{SEARCH_PROVIDER_ENV} to stop asking for it."
            ),
        )
    if provider.name == "searxng" and not endpoint:
        return None, Note(
            subject="web_search",
            detail=(
                f"provider 'searxng' needs {SEARCH_ENDPOINT_ENV} pointing at your own "
                f"instance (its JSON API must be enabled); web_search is not offered "
                f"until it is set."
            ),
        )
    searcher = provider_searcher(provider, key=key, endpoint=endpoint)
    return searcher, None


def system_prompt(loaded: Loaded, *, base: str = BASE_SYSTEM_PROMPT) -> str:
    """The base prompt plus RONIN.md and the repo map, in that order.

    Appended to the *system* text rather than injected as a user message so it lands
    inside the provider's cached stable prefix — which is the reason the repo map is
    rendered in a deterministic, path-ordered form in the first place.
    """
    suffix = loaded.system_suffix()
    return f"{base.rstrip()}\n\n{suffix}" if suffix else base.rstrip() + "\n"


def _engine_sandbox(sandbox: Sandbox | Unavailable) -> Sandbox | None:
    """``PolicyEngine.sandbox`` takes a ``Sandbox``, so an ``Unavailable`` becomes ``None``.

    ``Unavailable`` is not a ``Sandbox`` — it has ``isolates`` but no ``wrap`` and no
    ``name`` — so passing it would be a type error that happens to work today because
    the engine only reads ``isolates``. Rather than rely on that, an unavailable
    backend becomes ``None``, which the engine already handles as "no sandbox". The
    user is told by the note :func:`ronin.cli.spine.sandbox_note` produced at load.
    """
    if isinstance(sandbox, Unavailable):
        return None
    return sandbox


def subagent_policy_factory(parent: PolicyEngine) -> SubagentPolicyFactory:
    """The policy a nested turn runs under: standing permission, but no new questions.

    ``ronin.session``'s default denies every gated call, which made the shipped
    ``fixer`` — a subagent whose entire purpose is to edit code and rerun a failing
    test — unable to run the test. This is the seam that fixes it, and the reasoning is
    that **standing permission is not escalation**: a command the user has already
    allowlisted needs no new question, and :class:`~ronin.safety.policy.UnattendedAsker`
    turns everything that *would* need one into a refusal carrying feedback the child
    can report upward.

    Three deliberate choices, all of which are the difference between this and "the
    child can do whatever it likes":

    * **The parent's live ``effective_rules()``**, read at child construction rather
      than snapshotted at build time — so a "yes, remember for this session" the user
      gave two turns ago covers a child running the same command. That is safe because
      :meth:`~ronin.safety.policy.PolicyEngine.remembered_rule` records an
      :class:`~ronin.safety.policy.Exact` match on the byte-identical command and
      generalises to nothing; the child inherits one approved string, not a family.
    * **The same deny list, taint tracker and sandbox.** The unconditional floor is
      unconditional for children too, and content the parent fetched is still untrusted
      when a child quotes it.
    * **``persist=None``.** A child may act on a standing rule and may never write one:
      a permission nobody was asked about must not end up in a settings file.

    The child gets a fresh engine, so its decisions land in the child's audit trail and
    not the parent's — see the report.
    """

    def make(budget: Budget) -> Policy:
        del budget  # PolicyEngine.check_budget is handed the live budget per call.
        return PolicyEngine(
            rules=parent.effective_rules(),
            asker=UnattendedAsker(),
            denylist=parent.denylist,
            mode=parent.mode,
            taint=parent.taint,
            sandbox=parent.sandbox,
            persist=None,
        )

    return make


async def _connect_mcp(
    servers: tuple[McpServerConfig, ...],
    inner: ToolRegistry,
    transport_provider: TransportProvider | None,
    *,
    workspace_root: Path,
    oauth_driver: OAuthDriver | None = None,
) -> tuple[ToolRegistry, tuple[Note, ...], Closer]:
    """Connect every configured server and fold its tools in. Never raises.

    ``connect_all`` already refuses to let one bad server cost the session its local
    tools; this adds the other half — a note per failure, so "no tools appeared" is
    never a silent outcome — and registers the teardown as a closer.

    When the caller injects a ``transport_provider`` it owns transport entirely, including
    any authentication. Otherwise the default provider is built here: a real HTTP sender for
    network servers, and — for every ``auth: oauth`` server — a resolved ``Authorization``
    header. Resolution is non-interactive (it reuses or refreshes a stored token but never
    opens a browser at startup), so a server that has never been logged in becomes a failure
    note telling the operator to authorize it, and the session continues regardless.
    """
    oauth_failures: dict[str, str] = {}
    refresher_for: Callable[[McpServerConfig], TokenRefresher | None] | None = None
    driver = None
    if any(config.auth is AuthKind.OAUTH for config in servers):
        driver = oauth_driver or default_oauth_driver(workspace_root / ".ronin" / "mcp-auth")

        def refresher_for(config: McpServerConfig) -> TokenRefresher | None:
            return token_refresher(driver, config) if config.auth is AuthKind.OAUTH else None

    if transport_provider is not None:
        provider = transport_provider
    else:
        auth_headers: dict[str, Mapping[str, str]] = {}
        if driver is not None:
            resolved = await authorize_servers(servers, driver, interactive=False)
            auth_headers = dict(resolved.headers)
            oauth_failures = dict(resolved.failures)
        sender = (
            HttpxSender()
            if any(config.transport is not TransportKind.STDIO for config in servers)
            else None
        )
        provider = default_transport_provider(sender=sender, auth_headers=auth_headers)
    toolset = await connect_all(servers, provider, refresher_for=refresher_for)
    merged_failures = {**oauth_failures, **dict(toolset.failures)}
    notes: list[Note] = [
        Note(
            subject=f"mcp server {name!r}",
            detail=(
                f"{why} — its tools are absent, or present and answering with that "
                "message; the session continues with the local tools"
            ),
        )
        for name, why in sorted(merged_failures.items())
    ]
    skipped = sequence_note(
        "mcp tools",
        toolset.skipped,
        detail="could not be given a legal namespaced name and were dropped",
    )
    if skipped is not None:
        notes.append(skipped)
    if toolset.tools:
        inner = extend_registry(inner, toolset.tools)
    return inner, tuple(notes), toolset.aclose


__all__ = [
    "BASE_SYSTEM_PROMPT",
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_OUTPUT_BUDGET_CHARS",
    "LocalRuleWriter",
    "build_runtime",
    "load_workspace",
    "rule_to_json",
    "subagent_policy_factory",
    "system_prompt",
]
