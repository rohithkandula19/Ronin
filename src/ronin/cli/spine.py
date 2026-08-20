"""The seams the CLI layer meets at: where things live, what was loaded, what is wired.

``ronin.cli`` is the only package allowed to import every layer, so it is also the
only place where a mistake is invisible: each of the seven subsystems is
independently tested against fakes, and none of them can tell whether the *joins*
are right. This module exists to make the joins declarations rather than
assumptions.

Three values, in the order a run produces them:

1. :class:`Paths` — every on-disk location, derived once. Each subsystem exports its
   own relative path constant (``SESSIONS_SUBDIR``, ``CHECKPOINT_DIR``,
   ``AGENTS_SUBDIR``, …) and computing them separately in four call sites is how a
   session ends up writing its transcript somewhere its resume cannot find it. The
   constants are still the source of truth; this composes them.
2. :class:`Loaded` — the result of reading the workspace: settings, memory, repo map,
   subagents, hooks, verify spec, MCP servers, slash commands, sandbox backend. Pure
   data. Nothing here holds a socket, a subprocess, or a model.
3. :class:`Runtime` — the live objects, assembled. Typed against
   ``core.protocols.ToolRegistry`` rather than the concrete gated registry, so this
   module does not import the thing that imports it.

:class:`Note` is the other half of the contract and the reason ``Loaded`` is worth
having: a run that degrades must say so. A missing tree-sitter, an unparseable
settings layer, a dead MCP server and an absent sandbox are all *survivable*, and
all four have to be visible or the session silently does less than the user thinks
it is doing. ``/doctor`` prints these; it does not re-derive them.

One decision recorded here rather than left implicit: **``pinned_prefix_tokens`` is
computed in one place.** ``context.compaction.should_compact`` takes it as an
argument, and passing 0 — the default — means compaction believes the window is
emptier than it is by exactly the size of the repo map plus RONIN.md, which is the
one part of the prompt that never shrinks. Two callers computing it separately is
two chances to forget.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..agents.definitions import AGENTS_SUBDIR, AgentDefinition
from ..agents.hooks import HookConfig, HookRunner
from ..context.budget import estimate_tokens
from ..context.compaction import CompactionPolicy
from ..context.filestate import FileStateTracker
from ..context.memory import Memory, find_repo_root
from ..context.repomap import RepoMap
from ..core.protocols import ToolRegistry
from ..core.types import Mode
from ..ext.skills import SkillSet
from ..mcp.config import MCP_CONFIG_RELATIVE_PATH, McpServerConfig
from ..persistence.transcript import SESSIONS_SUBDIR, Transcript
from ..safety.injection import TaintTracker
from ..safety.policy import PolicyEngine
from ..safety.sandbox import NoSandbox, Sandbox, Unavailable
from ..safety.settings import LOCAL_SETTINGS, PROJECT_SETTINGS, USER_SETTINGS, Settings
from ..session import Session
from ..ui.commands import Registry as CommandRegistry
from ..verify.checkpoints import CHECKPOINT_DIR, CheckpointStore
from ..verify.spec import VerifySpec

#: An async no-argument teardown. MCP toolsets, shells and anything else a run opens
#: register one, instead of :class:`Runtime` growing a field per resource.
Closer = Callable[[], Awaitable[None]]

#: The one directory Ronin owns inside a workspace. Every subsystem's relative path
#: constant starts here; this is the shared root they agree on.
RONIN_DIRNAME = ".ronin"

#: Slash commands from markdown. ``ui.commands`` owns the loader and the string;
#: re-exported so ``Paths`` has one place per location like everything else.
COMMANDS_SUBDIR = Path(RONIN_DIRNAME) / "commands"

#: Where ``context.repomap`` keeps its mtime-invalidated parse cache. Inside
#: ``.ronin`` rather than a system temp dir so it dies with the workspace and a
#: stale cache can be deleted by hand without guessing a hash.
CACHE_SUBDIR = Path(RONIN_DIRNAME) / "cache"

#: ``agents.hooks`` loads from an explicit path; this is the conventional one.
HOOKS_FILENAME = "hooks.json"

#: Saved workflows. ``ext.skills`` owns the loader and the string; re-exported here so
#: ``Paths`` keeps one home per location like every other surface.
SKILLS_SUBDIR = Path(RONIN_DIRNAME) / "skills"


@dataclass(frozen=True, slots=True)
class Paths:
    """Every on-disk location one run uses, derived once from three inputs.

    ``workspace_root`` is the boundary tools refuse to leave and the anchor for the
    project settings layer; ``cwd`` is where the user actually is, which is *not*
    always the root and is what memory discovery walks up from; ``home`` is the user
    layer. All three are injected rather than read from the process, so a test
    constructs a whole workspace in ``tmp_path`` and nothing touches a real home
    directory.
    """

    workspace_root: Path
    home: Path
    cwd: Path

    @classmethod
    def discover(cls, cwd: Path | str = ".", *, home: Path | None = None) -> Paths:
        """Locate the workspace by walking up for a repo root, falling back to ``cwd``.

        A directory that is not a git repo is a legitimate workspace — ``ronin`` in a
        scratch folder must work — so the fallback is the answer, not an error.
        """
        here = Path(cwd).resolve()
        root = find_repo_root(here) or here
        return cls(workspace_root=root, home=(home or Path.home()).resolve(), cwd=here)

    @property
    def ronin_dir(self) -> Path:
        return self.workspace_root / RONIN_DIRNAME

    @property
    def sessions_dir(self) -> Path:
        return self.workspace_root / SESSIONS_SUBDIR

    @property
    def checkpoints_dir(self) -> Path:
        return self.workspace_root / CHECKPOINT_DIR

    @property
    def agents_dir(self) -> Path:
        return self.workspace_root / AGENTS_SUBDIR

    @property
    def commands_dir(self) -> Path:
        return self.workspace_root / COMMANDS_SUBDIR

    @property
    def skills_dir(self) -> Path:
        return self.workspace_root / SKILLS_SUBDIR

    @property
    def user_skills_dir(self) -> Path:
        """Skills that follow the user across workspaces, under the home layer."""
        return self.home / SKILLS_SUBDIR

    @property
    def cache_dir(self) -> Path:
        return self.workspace_root / CACHE_SUBDIR

    @property
    def mcp_config(self) -> Path:
        return self.workspace_root / MCP_CONFIG_RELATIVE_PATH

    @property
    def hooks_config(self) -> Path:
        return self.ronin_dir / HOOKS_FILENAME

    @property
    def user_settings(self) -> Path:
        return self.home / USER_SETTINGS

    @property
    def project_settings(self) -> Path:
        return self.workspace_root / PROJECT_SETTINGS

    @property
    def local_settings(self) -> Path:
        return self.workspace_root / LOCAL_SETTINGS

    def ensure(self) -> None:
        """Create the directories Ronin writes to. Idempotent, and never the home layer.

        ``~/.ronin`` is deliberately not created: the user layer is opt-in, and a
        first run that silently materializes a config directory in someone's home is
        a surprise. The wizard creates it, on request.
        """
        for directory in (self.ronin_dir, self.sessions_dir, self.cache_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def describe(self) -> tuple[str, ...]:
        """``(label, path, exists)`` rendered as lines, for ``/doctor``."""
        rows = (
            ("workspace", self.workspace_root),
            ("cwd", self.cwd),
            ("sessions", self.sessions_dir),
            ("checkpoints", self.checkpoints_dir),
            ("agents", self.agents_dir),
            ("commands", self.commands_dir),
            ("skills", self.skills_dir),
            ("mcp config", self.mcp_config),
            ("hooks", self.hooks_config),
            ("user settings", self.user_settings),
            ("project settings", self.project_settings),
            ("local settings", self.local_settings),
        )
        return tuple(
            f"{label:<17} {path}{'' if path.exists() else '  (absent)'}" for label, path in rows
        )


@dataclass(frozen=True, slots=True)
class Note:
    """One thing that did not load, and what the session does instead.

    Carried rather than logged because the only useful place for it is in front of
    the user: a run with no tree-sitter, an unreadable settings layer and a dead MCP
    server still works, and a user who is not told has no way to know why the agent
    suddenly cannot see their Rust files.
    """

    subject: str
    detail: str
    fatal: bool = False

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("Note.subject is required")
        if not self.detail:
            raise ValueError(
                "Note.detail is required — a note that names a problem without "
                "saying what happens instead is a log line, not a diagnostic"
            )

    def line(self) -> str:
        mark = "error" if self.fatal else "note"
        return f"{mark}: {self.subject} — {self.detail}"


@dataclass(frozen=True, slots=True)
class Loaded:
    """Everything read off disk for one run. Pure data, no live handles.

    Separating this from :class:`Runtime` is what makes ``/doctor`` and the first-run
    wizard possible without starting a session: both want to report on the workspace,
    and neither should open a shell, connect to an MCP server, or construct a model
    client to do it.
    """

    paths: Paths
    settings: Settings
    memory: Memory
    repo_map: RepoMap
    agents: Mapping[str, AgentDefinition] = field(default_factory=dict)
    hooks: HookConfig = field(default_factory=HookConfig)
    verify: VerifySpec = field(default_factory=VerifySpec)
    mcp_servers: tuple[McpServerConfig, ...] = ()
    commands: CommandRegistry = field(default_factory=CommandRegistry)
    skills: SkillSet = field(default_factory=SkillSet)
    sandbox: Sandbox | Unavailable = field(default_factory=NoSandbox)
    notes: tuple[Note, ...] = ()

    @property
    def mode(self) -> Mode:
        """The resolved permission mode. Settings own it; this is the one reader."""
        return self.settings.mode

    @property
    def healthy(self) -> bool:
        """No fatal note and no unparseable settings layer."""
        return self.settings.healthy and not any(note.fatal for note in self.notes)

    def with_notes(self, *extra: Note) -> Loaded:
        return replace(self, notes=(*self.notes, *extra))

    def pinned_prefix_tokens(self) -> int:
        """Tokens the transcript can never reclaim: RONIN.md plus the repo map.

        Handed to ``should_compact``/``maybe_compact``. The repo map reports its own
        estimate (it was budgeted against one); memory is estimated here from its
        rendered form, since that is the exact text the model sees.

        Both figures are ``len(text) / 4`` estimates, labelled as such wherever they
        are shown. See ``ronin.context``'s module docstring.
        """
        return estimate_tokens(self.memory.render()) + self.repo_map.token_estimate

    def compaction_policy(self, *, context_window: int) -> CompactionPolicy:
        """A policy for this window. One constructor, so the trigger cannot drift.

        The retention ceilings come from settings so the remedy compaction prints
        when retained results exceed the trigger is one the user can actually apply.
        Both default to ``None`` (no ceiling), which is what keeps a file edited in
        turn 3 answerable in turn 200; compaction escalates on its own when even
        that cannot fit.
        """
        return CompactionPolicy(
            context_window=context_window,
            max_retained_paths=self.settings.max_retained_paths,
            max_retained_chars=self.settings.max_retained_chars,
            escalate_to_fit=self.settings.compaction_escalate,
        )

    def system_suffix(self) -> str:
        """RONIN.md and the repo map, in the order they belong in the stable prefix.

        Appended to the system prompt rather than injected as a user message so it
        lands inside the provider's cached prefix — which is the whole reason the repo
        map is budgeted and rendered in a stable order.

        A map with no entries is dropped rather than rendered. ``RepoMap.render()``
        always emits its two header lines, so an empty workspace would otherwise pay
        for ``"# repo map — 0 of 0 file(s)"`` on every single request — a rounding
        error, but one that says nothing and costs forever.
        """
        rendered_map = self.repo_map.render() if self.repo_map.entries else ""
        # The skills catalog belongs in the stable prefix too, and belongs here rather
        # than in the skill tool's description because a human types /name without the
        # tool ever being consulted — so the one line per skill lives in exactly one
        # place, seen whichever way a skill is reached. Bodies are never in it.
        catalog = self.skills.catalog()
        parts = [text for text in (self.memory.render(), rendered_map, catalog) if text]
        return "\n\n".join(parts)

    def render(self) -> str:
        """The workspace as a human reads it. ``/doctor``'s body."""
        lines = [
            "paths",
            *(f"  {row}" for row in self.paths.describe()),
            "",
            "settings",
            *(f"  {row}" for row in self.settings.provenance()),
            "",
            f"mode              {self.mode.value}",
            f"sandbox           {self.sandbox.describe()}",
            f"memory            {len(self.memory.files)} file(s)"
            f"{': ' + ', '.join(self.memory.paths()) if self.memory.files else ''}",
            f"repo map          {self.repo_map.marker()}",
            f"subagents         {', '.join(sorted(self.agents)) or 'none'}",
            f"hooks             {len(self.hooks.hooks)} configured",
            f"mcp servers       {', '.join(c.name for c in self.mcp_servers) or 'none'}",
            f"slash commands    {len(self.commands.names())} "
            f"({len(self.commands.user)} user-defined)",
            f"skills            {len(self.skills.skills)}"
            f"{': ' + ', '.join(self.skills.names()) if self.skills.skills else ''}",
            "",
            "verify",
            *(f"  {row}" for row in self.verify.explain().splitlines()),
        ]
        if self.notes:
            lines += ["", "notes", *(f"  {note.line()}" for note in self.notes)]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Runtime:
    """One assembled session: the live objects, wired to each other.

    ``registry`` is typed as the ``core.protocols.ToolRegistry`` *protocol* on
    purpose. The concrete value is the gated registry from :mod:`ronin.cli.gate`,
    which imports this module for :class:`Paths`; typing the field against the
    protocol keeps that a one-way dependency instead of a cycle, and it is also the
    honest type — everything downstream of the gate only ever calls ``specs``,
    ``get`` and ``execute``.

    ``policy`` is the same object the loop is handed, so the audit trail a UI shows
    and the decisions the loop acted on cannot diverge.
    """

    loaded: Loaded
    session: Session
    registry: ToolRegistry
    policy: PolicyEngine
    files: FileStateTracker
    taint: TaintTracker
    hooks: HookRunner
    checkpoints: CheckpointStore
    compaction: CompactionPolicy
    system: str
    transcript: Transcript | None = None
    closers: tuple[Closer, ...] = ()

    async def aclose(self) -> None:
        """Release everything this run opened, in reverse order, best effort.

        Every closer runs even if an earlier one raises: a dead MCP server must not
        prevent the transcript being fsynced and closed, which is the one piece of
        state a crash makes unrecoverable.
        """
        errors: list[str] = []
        for close in reversed(self.closers):
            try:
                await close()
            # Deliberately broad: a closer is third-party-shaped (an MCP server, a
            # shell) and any exception from one must not strand the others.
            except Exception as exc:
                errors.append(f"{close!r}: {exc}")
        if self.transcript is not None:
            try:
                self.transcript.close()
            except OSError as exc:
                errors.append(f"transcript: {exc}")
        if errors:
            raise RuntimeError("errors while closing the runtime: " + "; ".join(errors))


def notes_from_settings(settings: Settings) -> tuple[Note, ...]:
    """Turn every unparseable settings layer into a note.

    ``load_settings`` deliberately survives a malformed layer rather than refusing to
    start — a typo in ``settings.local.json`` must not lock a user out of their own
    tool. That is only defensible if the survival is *reported*, which is here.
    """
    return tuple(
        Note(
            subject=f"settings layer {error.layer!r}",
            detail=(
                f"{error.message} — the layer was skipped, so its rules are not in "
                f"effect ({error.path if error.path is not None else 'no path'})"
            ),
        )
        for error in settings.errors
    )


def sandbox_note(sandbox: Sandbox | Unavailable) -> Note | None:
    """A note when sandboxing was asked for and is not available.

    Returning ``None`` for a working backend keeps the caller's code straight-line.
    An :class:`~ronin.safety.sandbox.Unavailable` is not an error — sandbox mode is
    off by default — but a user who turned it *on* and got no isolation must be told,
    because the alternative is believing commands are contained when they are not.
    """
    if isinstance(sandbox, Unavailable):
        return Note(
            subject="sandbox",
            detail=(
                f"{sandbox.describe()} — commands run unsandboxed, gated only by the policy engine"
            ),
        )
    return None


def sequence_note(subject: str, missing: Sequence[str], *, detail: str) -> Note | None:
    """A note naming what was skipped, or ``None`` when nothing was."""
    if not missing:
        return None
    return Note(subject=subject, detail=f"{detail}: {', '.join(missing)}")


__all__ = [
    "CACHE_SUBDIR",
    "COMMANDS_SUBDIR",
    "HOOKS_FILENAME",
    "RONIN_DIRNAME",
    "Closer",
    "Loaded",
    "Note",
    "Paths",
    "Runtime",
    "notes_from_settings",
    "sandbox_note",
    "sequence_note",
]
