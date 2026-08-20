"""Skills: saved workflows, discovered from disk, loaded on demand.

A skill is a **directory** with a ``SKILL.md`` inside it: frontmatter (``name``,
``description``, optional ``allowed-tools``, ``model``, and — for ported workflows —
``adapted-from``/``license``) over a markdown body. Companion files — a script, a
template, a checklist — sit beside ``SKILL.md`` and are referenced by relative path.

PROGRESSIVE DISCLOSURE is the whole point, and it is what makes six hundred skills cost
about two thousand tokens instead of a megabyte. At session start only the one-line
``name: description`` of each skill enters the prompt, through :meth:`SkillSet.catalog`.
The body — the actual instructions, which is where the tokens are — loads **only when
invoked**, and both ways to invoke it return that body as a *tool result*, so the
existing gate truncates it deterministically like any other tool output:

* the model calls the :class:`SkillTool` (``skill(name=...)``) when a catalog line fits
  the task;
* a human types ``/name`` and :meth:`SkillSet.invocation` hands the body back as the
  next turn's prompt.

One :class:`SkillSet` is the single source both read, so "the tool and the slash command
do the same thing" is true by construction rather than by two code paths kept in step.

DISCOVERY PRECEDENCE, lowest to highest: installed packages < ``~/.ronin/skills`` <
``./.ronin/skills``. **Local wins** — a skill you wrote in your own project overrides one
of the same name from your home directory, which overrides one a plugin installed. That
is the direction every other layered thing in this tree already resolves (project
settings beat user settings; nearer ``RONIN.md`` beats farther), and the reverse — a
package silently overriding the skill you wrote — is the one reading the ``<`` notation
must not be given. A shadowed skill is recorded as a note, never dropped in silence.

The frontmatter grammar is :func:`ronin.agents.definitions.parse_frontmatter`, the same
hand-rolled stdlib parser the subagent files use, told a different key set. One ``---``
grammar for the tree, not two that drift.

This module reads files but constructs no live handles and speaks to no model; it is
pure enough that every test here runs on a ``tmp_path`` with no session at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ..agents.definitions import FrontmatterError, parse_frontmatter
from ..core.types import DangerLevel, ToolResult
from ..tools.base import Example, Tool, ToolContext

#: The file that makes a directory a skill. Uppercase by the agentskills.io convention,
#: so a skill folder is recognisable in a listing and a plugin can carry a mix of skills
#: and other files without a manifest to disambiguate them.
SKILL_FILENAME = "SKILL.md"

#: Where project- and user-level skills live, relative to their respective roots.
SKILLS_SUBDIR = Path(".ronin") / "skills"

#: The role-workflow skills that ship *inside* Ronin (``ext/role_skills/*/SKILL.md``).
#: Discovered as the lowest-precedence tier, below installed plugins, so a user or a
#: project can always override a built-in workflow with one of their own. Packaged as
#: data files — see the wheel ``force-include`` in ``pyproject.toml``, without which
#: they load from a source tree but vanish from a ``pip install``.
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "role_skills"

#: The frontmatter keys a ``SKILL.md`` may set. ``allowed-tools`` and ``model`` are
#: optional hints; ``adapted-from`` and ``license`` exist for workflows ported from
#: another pack, so attribution travels with the file rather than living in a commit
#: message nobody reads. An unknown key is a load error, so ``allowed_tools`` (the wrong
#: spelling) is caught rather than silently ignored.
SKILL_KEYS: tuple[str, ...] = (
    "name",
    "description",
    "allowed-tools",
    "model",
    "adapted-from",
    "license",
)

#: Without a name there is nothing to invoke; without a description the catalog line is
#: blank and the model can never tell when the skill applies. Everything else is optional.
SKILL_REQUIRED: tuple[str, ...] = ("name", "description")

#: A skill name is an identifier: it is typed as ``/name`` and passed as ``skill(name=)``.
#: Same shape as a command and a subagent name, for one muscle memory across all three.
_NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


class SkillError(ValueError):
    """A ``SKILL.md`` that could not be turned into a :class:`Skill`.

    A ``ValueError`` so the loader's ``except (ValueError, OSError)`` catches it and
    turns one bad skill into a note, exactly as the other discovered-surface loaders do.
    """


def _valid_name(name: str) -> bool:
    return bool(name) and name[0] not in "-_" and all(ch in _NAME_OK for ch in name)


@dataclass(frozen=True, slots=True)
class Skill:
    """One saved workflow. The body is text; everything else describes or locates it."""

    name: str
    description: str
    body: str
    #: The directory that holds this skill's ``SKILL.md``. Companion files resolve
    #: against it, on demand — see :meth:`companion_files`.
    directory: Path
    #: Where it came from, for the catalog and for shadowing notes: ``"project"``,
    #: ``"user"``, or a plugin's label like ``"plugin:superpowers"``. A display string,
    #: not a trust level — trust is a separate axis added by the plugin layer.
    source: str = "project"
    #: Tools the skill declares it needs. **Advisory in this layer**: surfaced to the
    #: model in the loaded body, not enforced at the gate. The gate is the only place
    #: that can refuse a call, and it refuses on policy, not on which skill is "active" —
    #: there is no such notion, because a skill is instructions, not a mode.
    allowed_tools: tuple[str, ...] = ()
    #: A role the skill prefers (``main``/``plan``/``fast``). Recorded and surfaced;
    #: switching to it is the caller's choice via ``/model``, not an automatic effect.
    model: str = ""
    #: Attribution for a ported workflow, carried in the loaded body so credit is not
    #: lost. Set by the ``adapted-from:`` frontmatter key.
    adapted_from: str = ""
    #: The source pack's license, when the workflow was adapted from one.
    license: str = ""

    def catalog_line(self) -> str:
        """The one line that enters the prompt at session start. Never the body."""
        return f"- {self.name}: {self.description}"

    def companion_files(self) -> tuple[str, ...]:
        """Every file beside ``SKILL.md``, as sorted relative posix paths.

        Resolved on demand — a filesystem walk at *render* time, not at load time — so
        the catalog stays cheap no matter how many templates a skill ships. Returns
        ``()`` if the directory has vanished since load, rather than raising: a skill
        whose companions are gone can still offer its instructions.
        """
        if not self.directory.is_dir():
            return ()
        found: list[str] = []
        for path in sorted(self.directory.rglob("*")):
            if not path.is_file() or path.name == SKILL_FILENAME:
                continue
            if any(part.startswith(".") for part in path.relative_to(self.directory).parts):
                continue  # skip dotfiles and anything under a dot-directory
            found.append(path.relative_to(self.directory).as_posix())
        return tuple(found)

    def render(self) -> str:
        """What an invocation puts in front of the model: a header, then the body.

        The header is small and deterministic. It names the skill and, when they exist,
        the companion files (with the directory they resolve against) and the tools the
        skill expects — because a body that says "run ``scripts/check.py``" is useless
        unless the model can be told where ``scripts/`` is.
        """
        lines = [f"# skill: {self.name}", ""]
        lines.append(self.body.rstrip())
        extras: list[str] = []
        companions = self.companion_files()
        if companions:
            extras.append(
                f"companion files (relative to {self.directory}): " + ", ".join(companions)
            )
        if self.allowed_tools:
            extras.append("this skill expects these tools: " + ", ".join(self.allowed_tools))
        if self.model:
            extras.append(f"this skill prefers the {self.model!r} model role (switch with /model)")
        if self.adapted_from:
            credit = f"adapted from {self.adapted_from}"
            if self.license:
                credit += f" ({self.license})"
            extras.append(credit)
        if extras:
            lines += ["", "---", *extras]
        return "\n".join(lines).rstrip() + "\n"


def parse_skill(text: str, *, source: str, directory: Path) -> Skill:
    """Turn a ``SKILL.md``'s text into a :class:`Skill`, or raise :class:`SkillError`."""
    try:
        fields, body = parse_frontmatter(text, source=source, known_keys=SKILL_KEYS)
    except FrontmatterError as exc:
        raise SkillError(str(exc)) from exc
    missing = [key for key in SKILL_REQUIRED if not _scalar(fields.get(key))]
    if missing:
        raise SkillError(f"{source}: SKILL.md is missing required key(s): {', '.join(missing)}")
    name = _scalar(fields["name"])
    if not _valid_name(name):
        raise SkillError(
            f"{source}: skill name {name!r} must be lowercase letters, digits, '-' or "
            "'_', and may not start with '-' or '_'"
        )
    if not body.strip():
        raise SkillError(f"{source}: SKILL.md has no body — the instructions are the skill")
    return Skill(
        name=name,
        description=_scalar(fields["description"]),
        body=body,
        directory=directory,
        source=source,
        allowed_tools=_as_tuple(fields.get("allowed-tools")),
        model=_scalar(fields.get("model")),
        adapted_from=_scalar(fields.get("adapted-from")),
        license=_scalar(fields.get("license")),
    )


def load_skill_dir(directory: Path, *, source: str) -> Skill:
    """Read ``directory/SKILL.md`` into a :class:`Skill`. Raises on a missing/bad file."""
    skill_file = directory / SKILL_FILENAME
    text = skill_file.read_text(encoding="utf-8")  # OSError is the loader's to catch
    return parse_skill(text, source=source, directory=directory)


def discover(root: Path) -> Iterator[Path]:
    """Every skill directory under ``root`` — those containing a ``SKILL.md``.

    Sorted, so load order (and therefore any shadowing note) is deterministic. A missing
    ``root`` yields nothing: an absent ``.ronin/skills`` is the common case, not an error.
    """
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / SKILL_FILENAME).is_file():
            yield child


@dataclass(frozen=True)
class SkillSource:
    """One place skills are discovered from, and the label its skills carry.

    A value rather than a bare ``(path, label)`` tuple so the precedence list reads as
    what it is. Lowest precedence first: the plugin layer prepends installed roots, and
    the workspace loader appends user then project.
    """

    root: Path
    label: str


@dataclass(frozen=True, slots=True)
class SkillSet:
    """The skills available this session, and the two ways to reach one.

    Immutable and index-free: the skill count is small (hundreds at the extreme) and a
    lookup happens once per invocation, so a linear scan costs nothing and keeps the set
    a plain frozen value that a test can build by hand.
    """

    skills: tuple[Skill, ...] = ()

    def names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self.skills)

    def get(self, name: str) -> Skill | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    def catalog(self) -> str:
        """The progressive-disclosure block for the system prompt. ``""`` when empty.

        Names and descriptions only. The bodies are deliberately absent — that absence
        is the feature, and a test asserts it. The header tells the model both ways to
        pull a body in, so a catalog line is actionable on its own.
        """
        if not self.skills:
            return ""
        lines = [
            "# skills — saved workflows, loaded on demand",
            "Only names and descriptions are shown here. To load a skill's full "
            "instructions, call the skill(name=...) tool; a human can also type /name.",
            "",
        ]
        lines += [skill.catalog_line() for skill in self.skills]
        return "\n".join(lines)

    def invocation(self, line: str) -> SkillInvocation | None:
        """Parse a ``/name [argument]`` line into an invocation, or ``None``.

        ``None`` means "not a skill" — the line named nothing in this set — so the
        dispatcher can fall through to whatever else it tries. Mirrors the shape of the
        command parser so both are consulted the same way.
        """
        if not line.startswith("/"):
            return None
        head, _, rest = line[1:].strip().partition(" ")
        skill = self.get(head)
        if skill is None:
            return None
        return SkillInvocation(skill=skill, argument=rest.strip())


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    """A skill a human asked for by name, with whatever argument they typed."""

    skill: Skill
    argument: str = ""

    @property
    def prompt(self) -> str:
        """What the session runs as the next turn: the rendered body, plus the argument.

        ``$ARGUMENTS`` in the body is replaced when present; otherwise a non-empty
        argument is appended under a label. A skill with no ``$ARGUMENTS`` and no
        argument is just its instructions, which is the common case for a workflow.
        """
        rendered = self.skill.render()
        if "$ARGUMENTS" in rendered:
            return rendered.replace("$ARGUMENTS", self.argument)
        if self.argument:
            return f"{rendered}\nUser input: {self.argument}\n"
        return rendered


class SkillTool(Tool):
    """``skill(name=...)`` — load one skill's full instructions into the turn.

    Read-only and un-gated: loading a body changes nothing on disk and asks nothing of a
    human, it only brings saved instructions into context. The result is the skill's
    rendered body, so it flows through the same output clamp as every other tool result.

    Holds the :class:`SkillSet` rather than reaching for a global, which is why the same
    object answers the model's tool call and a human's ``/name``.
    """

    name = "skill"
    summary = "Load a skill — a saved workflow's full instructions — on demand."
    when_to_use = (
        "When a skill listed in the skills catalog fits the task at hand: its one-line "
        "description matched what you are about to do. Calling this loads that skill's "
        "full body — its steps, its checklist, the companion files it ships — into the "
        "conversation so you can follow it."
    )
    when_not_to_use = (
        "For a name that is not in the catalog — there is nothing to load and the call "
        "will say so. And do not reload a skill you already loaded this turn; its body "
        "is already above you."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill's name, exactly as the catalog lists it.",
            }
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    danger_level = DangerLevel.READ_ONLY
    requires_approval = False
    examples: ClassVar[Sequence[Example]] = (
        Example(
            call='skill(name="plan")',
            result="the plan workflow's full instructions, ready to follow",
        ),
    )

    def __init__(self, skills: SkillSet) -> None:
        self._skills = skills

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        del ctx  # a skill body is workspace-independent; nothing here touches the tree
        raw = args.get("name")
        if not isinstance(raw, str) or not raw.strip():
            return ToolResult(
                ok=False,
                error="'name' is required and must be the name of a skill from the catalog.",
            )
        skill = self._skills.get(raw.strip())
        if skill is None:
            known = ", ".join(self._skills.names()) or "none are installed"
            return ToolResult(
                ok=False,
                error=f"there is no skill called {raw.strip()!r}. Available skills: {known}.",
            )
        return ToolResult(ok=True, content=skill.render())


def load_skills(
    project_root: Path,
    home: Path,
    *,
    extra: Sequence[SkillSource] = (),
) -> tuple[SkillSet, tuple[str, ...]]:
    """Discover every skill, resolve name collisions by precedence, return notes.

    ``extra`` is the lowest-precedence tier — the plugin layer passes each installed
    plugin's ``skills/`` here — followed by the user root, then the project root, which
    wins. The returned notes name every shadowing and every skill that failed to load,
    because a skill silently missing is a workflow a user thinks they have and does not.

    Notes are plain strings; :func:`ronin.cli.wire.load_workspace` lifts them into
    :class:`~ronin.cli.spine.Note` values, the same treatment the command loader gets.
    """
    sources: list[SkillSource] = [
        *extra,
        SkillSource(root=home / SKILLS_SUBDIR, label="user"),
        SkillSource(root=project_root / SKILLS_SUBDIR, label="project"),
    ]
    by_name: dict[str, Skill] = {}
    origin: dict[str, str] = {}
    notes: list[str] = []
    for tier in sources:
        for directory in discover(tier.root):
            try:
                skill = load_skill_dir(directory, source=tier.label)
            except (SkillError, OSError) as exc:
                notes.append(f"skill at {directory} was skipped: {exc}")
                continue
            if skill.name in by_name:
                notes.append(
                    f"skill {skill.name!r} from {tier.label} shadows the one from "
                    f"{origin[skill.name]}"
                )
            by_name[skill.name] = skill
            origin[skill.name] = tier.label
    ordered = tuple(sorted(by_name.values(), key=lambda skill: skill.name))
    return SkillSet(skills=ordered), tuple(notes)


def _scalar(value: Any) -> str:
    """A frontmatter value as a single string. A list collapses to its first item."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, tuple) and value:
        first = value[0]
        return first.strip() if isinstance(first, str) else str(first)
    return ""


def _as_tuple(value: Any) -> tuple[str, ...]:
    """A frontmatter value as a tuple of strings, tolerating scalar or list."""
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, Iterable):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


__all__ = [
    "BUILTIN_SKILLS_DIR",
    "SKILLS_SUBDIR",
    "SKILL_FILENAME",
    "SKILL_KEYS",
    "SKILL_REQUIRED",
    "Skill",
    "SkillError",
    "SkillInvocation",
    "SkillSet",
    "SkillSource",
    "SkillTool",
    "discover",
    "load_skill_dir",
    "load_skills",
    "parse_skill",
]
