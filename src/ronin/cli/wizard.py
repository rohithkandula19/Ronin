"""First run: what would be created, then create it. Two functions, no prompting.

The split is the design. :func:`plan_first_run` inspects a workspace and returns a
:class:`WizardPlan` — every file it would write, its exact content, and *why* — and
:func:`apply_plan` writes it. Neither reads stdin, prints, or asks anything: the
question ("shall I?") belongs to the UI, and keeping it out of here is what makes the
wizard testable at all. A wizard that mixes the decision into the writing can only be
tested by driving a terminal.

Three rules the plan obeys:

1. **Nothing is overwritten.** An existing ``RONIN.md`` is somebody's standing
   instructions, and a first-run wizard that clobbers it is data loss with a friendly
   banner. A file that exists is reported as present and skipped.
2. **Content comes from evidence.** The ``RONIN.md`` draft is
   :func:`ronin.context.memory.propose_bootstrap`, which reads ``package.json``,
   ``pyproject.toml``, ``Makefile``, ``justfile``, ``Cargo.toml`` and the CI workflows,
   labels every command with the file it came from, and reports an absent test command
   as absent. A drafted-but-wrong ``make test`` is worse than a blank section.
3. **``~/.ronin`` only on request.** ``Paths.ensure()`` deliberately does not create
   the home layer, and neither does this unless ``include_user_layer`` is set: a first
   run that silently materialises a config directory in somebody's home is a surprise.

What the wizard deliberately does **not** do is edit ``.gitignore``. Adding lines to a
file the wizard did not write, in a repo whose history it does not own, is a different
kind of act from creating ``.ronin/settings.json`` — so the plan carries the exact
lines as a note, and ``ronin.cli.doctor`` reports the same thing as a check with the
patch attached. The user applies it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..context.memory import RONIN_FILENAME, discover_commands, propose_bootstrap
from ..safety.settings import RULES_KEY
from .doctor import GITIGNORE_PATCH
from .spine import Paths

#: The committed project layer, as created. Every key is the builtin default, so the
#: file changes nothing on its own — it exists to be the place a team edits, and an
#: empty rule list is the shape a rule goes into.
STARTER_PROJECT_SETTINGS: str = (
    json.dumps({"mode": "ask", RULES_KEY: []}, indent=2, sort_keys=True) + "\n"
)


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """One file the wizard would create, with its content and its justification."""

    path: Path
    content: str
    reason: str
    exists: bool = False

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError(f"planned file {self.path} has no content")
        if not self.reason:
            raise ValueError(
                f"planned file {self.path} has no reason — the user is being asked to "
                "accept a file, and 'why' is the only thing that makes that answerable"
            )

    @property
    def will_write(self) -> bool:
        """False for a file that already exists. The wizard never overwrites."""
        return not self.exists

    def line(self) -> str:
        mark = "skip " if self.exists else "write"
        tail = " (already exists)" if self.exists else ""
        return f"  {mark} {self.path}{tail}\n        {self.reason}"


@dataclass(frozen=True, slots=True)
class WizardPlan:
    """Exactly what a first run would do, as data a UI can print and a test can assert."""

    paths: Paths
    files: tuple[PlannedFile, ...] = ()
    directories: tuple[Path, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def writes(self) -> tuple[PlannedFile, ...]:
        return tuple(planned for planned in self.files if planned.will_write)

    @property
    def empty(self) -> bool:
        """Nothing to do: every file exists and every directory is present."""
        return not self.writes and not self.missing_directories

    @property
    def missing_directories(self) -> tuple[Path, ...]:
        return tuple(directory for directory in self.directories if not directory.is_dir())

    def render(self) -> str:
        parts = [f"first-run plan for {self.paths.workspace_root}", ""]
        if self.missing_directories:
            parts.append("directories")
            parts.extend(f"  create {directory}" for directory in self.missing_directories)
            parts.append("")
        parts.append("files")
        parts.extend(planned.line() for planned in self.files)
        if self.notes:
            parts.extend(["", "notes", *(f"  {note}" for note in self.notes)])
        if self.empty:
            parts.extend(["", "nothing to do — this workspace is already set up"])
        return "\n".join(parts) + "\n"


def plan_first_run(
    paths: Paths,
    *,
    include_user_layer: bool = False,
    memory: bool = True,
    project_settings: bool = True,
) -> WizardPlan:
    """Inspect ``paths`` and describe the first run. Touches nothing.

    ``memory`` and ``project_settings`` are separable because they are separable
    decisions: plenty of users want the drafted ``RONIN.md`` and no committed settings
    file, and the reverse happens in a repo that already has instructions.
    """
    root = paths.workspace_root
    directories: list[Path] = [paths.ronin_dir, paths.sessions_dir, paths.cache_dir]
    if include_user_layer:
        # `Paths.ensure()` will not create this, on purpose. Only an explicit request
        # puts a directory in somebody's home.
        directories.append(paths.home / ".ronin")

    files: list[PlannedFile] = []
    notes: list[str] = []

    if memory:
        target = root / RONIN_FILENAME
        found = discover_commands(root)
        detail = (
            f"drafted from {len(found)} command(s) found in this repo, each labelled "
            "with the file it came from"
            if found
            else (
                "no build/test/lint command could be found in this repo, so the draft "
                "says so per section rather than guessing one"
            )
        )
        files.append(
            PlannedFile(
                path=target,
                content=propose_bootstrap(root),
                reason=(
                    "the standing instructions the model reads on every turn — "
                    f"{detail}"
                ),
                exists=target.exists(),
            )
        )

    if project_settings:
        target = paths.project_settings
        files.append(
            PlannedFile(
                path=target,
                content=STARTER_PROJECT_SETTINGS,
                reason=(
                    "the committed permission layer, shared with the team; every rule "
                    "here is visible in /doctor and attributable to this file"
                ),
                exists=target.exists(),
            )
        )

    notes.append(
        "this wizard does not edit .gitignore. add these lines yourself so the local "
        "layer stays out of git and the shared config stays in it:\n"
        + "\n".join(f"    {line}" for line in GITIGNORE_PATCH.splitlines())
    )
    if not include_user_layer:
        notes.append(
            f"{paths.home / '.ronin'} was not planned: the cross-project user layer is "
            "opt-in. re-run with the user layer enabled to create it"
        )

    return WizardPlan(
        paths=paths,
        files=tuple(files),
        directories=tuple(directories),
        notes=tuple(notes),
    )


def apply_plan(plan: WizardPlan) -> tuple[Path, ...]:
    """Create the plan's directories and write the files that do not exist yet.

    Returns the paths actually written, sorted, so a caller can report them without
    re-deriving which ones were skipped. Directories are created first: a planned file
    inside ``.ronin/`` has nowhere to go otherwise.

    Existing files are left alone even if their content differs from the plan — see
    rule 1 in this module's docstring. Re-running the wizard on a set-up workspace is
    therefore a no-op and returns ``()``.
    """
    for directory in plan.directories:
        directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for planned in plan.files:
        if not planned.will_write or planned.path.exists():
            continue
        planned.path.parent.mkdir(parents=True, exist_ok=True)
        # Explicit "\n" newline: these files are read back as bytes by tests and, more
        # to the point, a RONIN.md with CRLF endings renders a stray \r into the model's
        # prompt on every turn.
        planned.path.write_text(planned.content, encoding="utf-8", newline="\n")
        written.append(planned.path)
    return tuple(sorted(written))


__all__ = [
    "STARTER_PROJECT_SETTINGS",
    "PlannedFile",
    "WizardPlan",
    "apply_plan",
    "plan_first_run",
]
