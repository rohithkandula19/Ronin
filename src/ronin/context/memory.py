"""RONIN.md — the standing instructions the model gets on every single turn.

This is the cheapest quality lever in the system and the one most often left
empty, which is why :func:`propose_bootstrap` exists: a draft assembled from what
is actually in the repo is far more likely to get kept than a blank file.

Three rules shape the module.

1. **Most local wins.** Files load global-first, then outermost project file to
   innermost, and render in that order, because a model reading top to bottom
   treats the last statement as the operative one. A monorepo package can
   therefore override the repo's default test command by saying so locally.
2. **Provenance is rendered.** Every section is labelled with the file it came
   from. When the model follows a rule the user does not recognise, the answer to
   "where did that come from" has to be in the prompt, not in a support thread.
3. **Nothing is invented.** :func:`propose_bootstrap` reports an absent test
   command as absent. A drafted-but-wrong ``make test`` is worse than no line at
   all: the model will run it, it will fail, and the failure looks like the
   repo's fault.

The home directory is an injected parameter and defaults to *no global memory*
rather than to ``Path.home()``. A test that accidentally reads the developer's
real ``~/.ronin/RONIN.md`` is a test whose result depends on whose laptop it ran
on; the orchestrator passes the real home explicitly.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

#: The per-project memory file, at the repo root or in any directory above cwd.
RONIN_FILENAME = "RONIN.md"

#: Global memory, relative to the injected home directory.
GLOBAL_SUBPATH = Path(".ronin") / RONIN_FILENAME

#: What ``# ronin: remember X`` appends under. Dated, so a user reading the file
#: months later can tell a rule from last week from one from the first session.
REMEMBERED_HEADING = "## Remembered"

#: How far up the tree to look for the ``.git`` boundary before giving up. A
#: bounded walk, because a cwd outside any repo would otherwise walk to ``/``.
MAX_PARENTS = 64


class MemoryScope(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class MemoryFile:
    """One loaded RONIN.md, with enough provenance to render a label."""

    path: str
    scope: MemoryScope
    text: str
    #: Distance from the repo root in directories; ``-1`` for global memory.
    #: Higher is more local, and more local wins.
    depth: int

    def label(self) -> str:
        if self.scope is MemoryScope.GLOBAL:
            return f"global — {self.path}"
        return f"project — {self.path}"


@dataclass(frozen=True, slots=True)
class Memory:
    """Every RONIN.md in effect, in precedence order (least local first)."""

    files: tuple[MemoryFile, ...] = ()
    root: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(f.text.strip() for f in self.files)

    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)

    def render(self) -> str:
        """Labelled sections, least local first. Empty string when there is nothing.

        Returning ``""`` rather than a header with no content matters: this text
        goes into the stable prefix, and an empty-but-present section is a block
        the model has to read past on every turn for no information.
        """
        sections = [f for f in self.files if f.text.strip()]
        if not sections:
            return ""
        parts = [
            "# memory (RONIN.md)",
            "",
            "Ordered least-local first. Where two files conflict, the LAST one wins.",
        ]
        for file in sections:
            # A rule between sections, because a RONIN.md carries its own `##`
            # headings and without a separator the provenance label reads as a
            # sibling of the content it introduces.
            parts.append("")
            parts.append("---")
            parts.append(f"## {file.label()}")
            parts.append("")
            parts.append(file.text.strip())
        return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# discovery + loading
# --------------------------------------------------------------------------- #


def find_repo_root(start: Path) -> Path | None:
    """The nearest ancestor containing ``.git``, or ``None`` outside a repo.

    ``.git`` may be a directory or a file (a worktree or submodule writes a file),
    so existence is the test, not ``is_dir``.
    """
    current = start if start.is_dir() else start.parent
    for _ in range(MAX_PARENTS):
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent
    return None


def memory_paths(
    cwd: Path, *, home: Path | None = None, root: Path | None = None
) -> tuple[Path, ...]:
    """Candidate memory files in precedence order: global, outermost → innermost.

    Stops at the ``.git`` boundary. Without that stop, a repo cloned into a home
    directory that has its own RONIN.md would silently inherit it as project
    memory, and "why is the agent running the wrong test command" becomes a
    filesystem-layout question.
    """
    candidates: list[Path] = []
    if home is not None:
        candidates.append(home / GLOBAL_SUBPATH)
    boundary = root if root is not None else find_repo_root(cwd)
    chain: list[Path] = []
    current = cwd if cwd.is_dir() else cwd.parent
    for _ in range(MAX_PARENTS):
        chain.append(current)
        if boundary is not None and current == boundary:
            break
        if current.parent == current:
            break
        current = current.parent
    if boundary is not None and boundary not in chain:
        # cwd is not under the boundary we were handed; trust the explicit root.
        chain = [boundary]
    candidates.extend(directory / RONIN_FILENAME for directory in reversed(chain))
    return tuple(candidates)


def load_memory(cwd: Path, *, home: Path | None = None, root: Path | None = None) -> Memory:
    """Load every RONIN.md in effect for ``cwd``. Missing files are simply absent."""
    boundary = root if root is not None else find_repo_root(cwd)
    files: list[MemoryFile] = []
    for path in memory_paths(cwd, home=home, root=boundary):
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        is_global = home is not None and path == home / GLOBAL_SUBPATH
        files.append(
            MemoryFile(
                path=_display_path(path, boundary),
                scope=MemoryScope.GLOBAL if is_global else MemoryScope.PROJECT,
                text=text,
                depth=-1 if is_global else _depth(path.parent, boundary),
            )
        )
    return Memory(files=tuple(files), root=str(boundary) if boundary else "")


def _display_path(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return str(path)


def _depth(directory: Path, root: Path | None) -> int:
    if root is None:
        return 0
    try:
        return len(directory.relative_to(root).parts)
    except ValueError:
        return 0


# --------------------------------------------------------------------------- #
# remember
# --------------------------------------------------------------------------- #


def remember(
    text: str,
    *,
    cwd: Path,
    root: Path | None = None,
    today: date | None = None,
) -> Path:
    """Append ``text`` to the nearest project RONIN.md, creating it if absent.

    "Nearest" is the closest existing project file walking up from ``cwd`` to the
    repo root. When none exists the file is created at the **repo root**, not in
    ``cwd``: a rule the user just stated ("always run the tests with -x") is almost
    never about the subdirectory they happened to be standing in, and a scattering
    of one-line RONIN.md files is worse than one that grows.

    Idempotent on the exact bullet text, checked against the whole file rather than
    against today's section — re-remembering the same rule tomorrow must not add a
    second copy.
    """
    entry = text.strip()
    if not entry:
        raise ValueError("remember() needs something to remember")
    boundary = root if root is not None else find_repo_root(cwd)
    target = _nearest_memory_file(cwd, boundary)
    bullet = f"- {entry}"

    original = b""
    if target.exists():
        original = target.read_bytes()
    body = original.decode("utf-8", errors="replace")
    newline = "\r\n" if b"\r\n" in original else "\n"
    lines = body.split(newline) if body else []
    if bullet in (line.rstrip() for line in lines):
        return target

    heading = f"{REMEMBERED_HEADING} {(today or date.today()).isoformat()}"
    updated = _insert_bullet(lines, heading, bullet)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(newline.join(updated).encode("utf-8"))
    return target


def _nearest_memory_file(cwd: Path, root: Path | None) -> Path:
    directory = cwd if cwd.is_dir() else cwd.parent
    for _ in range(MAX_PARENTS):
        candidate = directory / RONIN_FILENAME
        if candidate.exists():
            return candidate
        if root is not None and directory == root:
            break
        if directory.parent == directory:
            break
        directory = directory.parent
    return (root or (cwd if cwd.is_dir() else cwd.parent)) / RONIN_FILENAME


def _insert_bullet(lines: Sequence[str], heading: str, bullet: str) -> list[str]:
    """Put ``bullet`` at the end of ``heading``'s section, creating the section."""
    out = list(lines)
    try:
        start = next(i for i, line in enumerate(out) if line.strip() == heading)
    except StopIteration:
        while out and not out[-1].strip():
            out.pop()
        if out:
            out.append("")
        out.extend([heading, "", bullet, ""])
        return out
    end = len(out)
    for index in range(start + 1, len(out)):
        if out[index].startswith("## "):
            end = index
            break
    insert_at = end
    while insert_at > start + 1 and not out[insert_at - 1].strip():
        insert_at -= 1
    out.insert(insert_at, bullet)
    return out


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #


class CommandKind(StrEnum):
    """The categories a bootstrap draft is organised into, in rendered order."""

    BUILD = "build"
    TEST = "test"
    LINT = "lint"
    TYPECHECK = "typecheck"
    FORMAT = "format"
    RUN = "run"


@dataclass(frozen=True, slots=True)
class FoundCommand:
    """A command with the file and key it was read out of.

    ``evidence`` is not decoration: it is what makes a wrong line correctable by
    the user, and it is the assertion a test makes to prove the command was found
    rather than assumed.
    """

    kind: CommandKind
    command: str
    evidence: str


#: Files inspected, in the order they are inspected. Reported in the draft so a
#: user can see that an absent command means "not found here", not "not looked for".
BOOTSTRAP_SOURCES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "Makefile",
    "justfile",
    "Cargo.toml",
    ".github/workflows/*.yml",
)

_SCRIPT_KINDS: Mapping[str, CommandKind] = {
    "build": CommandKind.BUILD,
    "compile": CommandKind.BUILD,
    "test": CommandKind.TEST,
    "tests": CommandKind.TEST,
    "test:unit": CommandKind.TEST,
    "lint": CommandKind.LINT,
    "check": CommandKind.LINT,
    "typecheck": CommandKind.TYPECHECK,
    "type-check": CommandKind.TYPECHECK,
    "types": CommandKind.TYPECHECK,
    "format": CommandKind.FORMAT,
    "fmt": CommandKind.FORMAT,
    "dev": CommandKind.RUN,
    "start": CommandKind.RUN,
    "serve": CommandKind.RUN,
}

_MAKE_KINDS: Mapping[str, CommandKind] = {
    "build": CommandKind.BUILD,
    "test": CommandKind.TEST,
    "tests": CommandKind.TEST,
    "lint": CommandKind.LINT,
    "check": CommandKind.LINT,
    "typecheck": CommandKind.TYPECHECK,
    "mypy": CommandKind.TYPECHECK,
    "format": CommandKind.FORMAT,
    "fmt": CommandKind.FORMAT,
    "run": CommandKind.RUN,
    "dev": CommandKind.RUN,
    "serve": CommandKind.RUN,
}

_MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*:(?!=)")
_JUST_RECIPE = re.compile(r"^([a-z0-9][a-z0-9_\-]*)(?:\s+[^:=]*)?:(?!=)")
_WORKFLOW_RUN = re.compile(r"^(\s*)-?\s*run:\s*(.*)$")

#: Workflow ``run:`` lines are classified by substring. Ordered: the first match
#: wins, so "pytest" beats the generic "test" and a lint step that happens to
#: mention "build" is not filed as a build.
_WORKFLOW_HINTS: tuple[tuple[str, CommandKind], ...] = (
    ("pytest", CommandKind.TEST),
    ("ruff", CommandKind.LINT),
    ("mypy", CommandKind.TYPECHECK),
    ("tsc", CommandKind.TYPECHECK),
    ("eslint", CommandKind.LINT),
    ("lint", CommandKind.LINT),
    ("cargo test", CommandKind.TEST),
    ("go test", CommandKind.TEST),
    ("test", CommandKind.TEST),
    ("build", CommandKind.BUILD),
)


def discover_commands(root: Path) -> tuple[FoundCommand, ...]:
    """Every build/test/lint command with evidence in ``root``. Never invents one.

    Deterministic: sources are read in :data:`BOOTSTRAP_SOURCES` order and the
    first evidence for a given command string wins, so two runs over the same tree
    produce byte-identical output.
    """
    found: list[FoundCommand] = []
    found.extend(_from_package_json(root))
    found.extend(_from_pyproject(root))
    found.extend(_from_makefile(root))
    found.extend(_from_justfile(root))
    found.extend(_from_cargo(root))
    found.extend(_from_workflows(root))
    return _dedupe(found)


def propose_bootstrap(root: Path) -> str:
    """Draft a RONIN.md from evidence in ``root``. Returns text; writes nothing.

    Writing is a separate, explicit call precisely because this is a *proposal*:
    the user should see it before it becomes the thing the model obeys forever.
    """
    found = discover_commands(root)
    parts = [
        f"# {RONIN_FILENAME}",
        "",
        "<!-- Drafted by ronin from files in this repo. Every command below was read",
        "     out of a real file, named in the evidence comment; nothing was inferred.",
        "     Delete what is wrong and fill in the empty sections. -->",
    ]
    for kind in CommandKind:
        parts.append("")
        parts.append(f"## {kind.value.capitalize()}")
        entries = [f for f in found if f.kind is kind]
        if entries:
            parts.extend(f"- `{f.command}`  <!-- found in {f.evidence} -->" for f in entries)
        else:
            parts.append(
                f"- none found: no {kind.value} command appears in "
                f"{', '.join(BOOTSTRAP_SOURCES)}. Add yours here."
            )
    parts.extend(
        [
            "",
            "## Style rules",
            "- (not detectable from files — write the conventions you actually enforce)",
            "",
            "## Gotchas",
            "- (write the things that waste an hour: flaky suites, required env vars,",
            "  services that must be running first)",
            "",
            "## Inspected",
        ]
    )
    parts.extend(f"- {source}: {_presence(root, source)}" for source in BOOTSTRAP_SOURCES)
    return "\n".join(parts) + "\n"


def _presence(root: Path, source: str) -> str:
    if "*" in source:
        directory = root / Path(source).parent
        matches = sorted(directory.glob(Path(source).name)) if directory.is_dir() else []
        return f"{len(matches)} file(s)" if matches else "none"
    return "found" if (root / source).exists() else "missing"


def _dedupe(found: Iterable[FoundCommand]) -> tuple[FoundCommand, ...]:
    seen: set[str] = set()
    out: list[FoundCommand] = []
    for item in found:
        if item.command in seen:
            continue
        seen.add(item.command)
        out.append(item)
    return tuple(out)


def _load_json(path: Path) -> Mapping[str, Any] | None:
    try:
        data: object = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _from_package_json(root: Path) -> list[FoundCommand]:
    data = _load_json(root / "package.json")
    if data is None:
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    runner = _node_runner(root, data)
    out: list[FoundCommand] = []
    for name, kind in _SCRIPT_KINDS.items():
        if name in scripts:
            out.append(
                FoundCommand(
                    kind=kind,
                    command=f"{runner} {name}",
                    evidence=f"package.json scripts.{name}",
                )
            )
    return out


def _node_runner(root: Path, data: Mapping[str, Any]) -> str:
    """``pnpm``/``yarn``/``bun``/``npm run``, decided by declaration then lockfile."""
    declared = data.get("packageManager")
    if isinstance(declared, str):
        name = declared.split("@", 1)[0].strip()
        if name in {"pnpm", "yarn", "bun"}:
            return name
        if name == "npm":
            return "npm run"
    for lockfile, runner in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
    ):
        if (root / lockfile).exists():
            return runner
    return "npm run"


def _from_pyproject(root: Path) -> list[FoundCommand]:
    path = root / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    tool = data.get("tool")
    tools: Mapping[str, Any] = tool if isinstance(tool, dict) else {}
    out: list[FoundCommand] = []
    if "pytest" in tools:
        out.append(FoundCommand(CommandKind.TEST, "pytest", "pyproject.toml [tool.pytest]"))
    if "ruff" in tools:
        out.append(FoundCommand(CommandKind.LINT, "ruff check", "pyproject.toml [tool.ruff]"))
    if "mypy" in tools:
        out.append(FoundCommand(CommandKind.TYPECHECK, "mypy", "pyproject.toml [tool.mypy]"))
    if "black" in tools:
        out.append(FoundCommand(CommandKind.FORMAT, "black .", "pyproject.toml [tool.black]"))
    for name, kind, command in (
        ("pytest", CommandKind.TEST, "pytest"),
        ("ruff", CommandKind.LINT, "ruff check"),
        ("mypy", CommandKind.TYPECHECK, "mypy"),
    ):
        where = _declared_dev_dependency(data, name)
        if where:
            out.append(FoundCommand(kind, command, f"pyproject.toml {where}"))
    return out


def _declared_dev_dependency(data: Mapping[str, Any], name: str) -> str:
    """Where ``name`` is declared as a dev dependency, or ``""``.

    Covers PEP 735 ``[dependency-groups]`` and the older optional-dependencies
    style, because a repo pinning ruff in either place is evidence that ``ruff``
    is the lint command here.
    """
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group, entries in groups.items():
            if isinstance(entries, list) and _mentions(entries, name):
                return f"[dependency-groups] {group}"
    project = data.get("project")
    if isinstance(project, dict):
        extras = project.get("optional-dependencies")
        if isinstance(extras, dict):
            for extra, entries in extras.items():
                if isinstance(entries, list) and _mentions(entries, name):
                    return f"[project.optional-dependencies] {extra}"
    return ""


def _mentions(entries: Sequence[object], name: str) -> bool:
    return any(
        isinstance(entry, str) and re.match(rf"^{re.escape(name)}\b", entry.strip())
        for entry in entries
    )


def _from_makefile(root: Path) -> list[FoundCommand]:
    return _from_recipes(root / "Makefile", _MAKE_TARGET, _MAKE_KINDS, "make", "Makefile")


def _from_justfile(root: Path) -> list[FoundCommand]:
    return _from_recipes(root / "justfile", _JUST_RECIPE, _MAKE_KINDS, "just", "justfile")


def _from_recipes(
    path: Path,
    pattern: re.Pattern[str],
    kinds: Mapping[str, CommandKind],
    runner: str,
    label: str,
) -> list[FoundCommand]:
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: list[FoundCommand] = []
    for line in text.splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        target = match.group(1)
        kind = kinds.get(target)
        if kind is None:
            continue
        out.append(FoundCommand(kind, f"{runner} {target}", f"{label} target {target!r}"))
    return out


def _from_cargo(root: Path) -> list[FoundCommand]:
    """``cargo build``/``cargo test`` for a crate.

    The evidence is the manifest itself: for a Cargo project these two commands
    are not a guess about this repo, they are how the toolchain is defined to work.
    A crate-specific command (a custom xtask, say) is not inferred.
    """
    path = root / "Cargo.toml"
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    if not ({"package", "workspace"} & set(data)):
        return []
    return [
        FoundCommand(CommandKind.BUILD, "cargo build", "Cargo.toml (cargo manifest)"),
        FoundCommand(CommandKind.TEST, "cargo test", "Cargo.toml (cargo manifest)"),
    ]


def _from_workflows(root: Path) -> list[FoundCommand]:
    """``run:`` steps from GitHub workflows, classified by substring.

    Deliberately regex over a YAML parser: pulling in a YAML dependency to read
    shell strings out of CI would be the tail wagging the dog, and a missed step
    only costs a line in a draft the user is going to edit anyway. Block scalars
    (``run: |``) are followed by taking the more-indented lines beneath them.
    """
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return []
    out: list[FoundCommand] = []
    for path in sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]):
        try:
            lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        label = path.relative_to(root).as_posix()
        for command in _workflow_commands(lines):
            kind = _classify(command)
            if kind is not None:
                out.append(FoundCommand(kind, command, f"{label} run step"))
    return out


def _workflow_commands(lines: Sequence[str]) -> list[str]:
    commands: list[str] = []
    index = 0
    while index < len(lines):
        match = _WORKFLOW_RUN.match(lines[index])
        if match is None:
            index += 1
            continue
        indent, rest = match.group(1), match.group(2).strip()
        if rest and rest not in {"|", ">", "|-", ">-"}:
            commands.append(rest.strip("'\""))
            index += 1
            continue
        index += 1
        while index < len(lines):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip()) <= len(indent):
                break
            if line.strip():
                commands.append(line.strip())
            index += 1
    return commands


def _classify(command: str) -> CommandKind | None:
    lowered = command.lower()
    for needle, kind in _WORKFLOW_HINTS:
        if needle in lowered:
            return kind
    return None
