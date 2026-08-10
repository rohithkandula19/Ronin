"""What "verified" means for *this* repo, discovered from evidence in the tree.

The governing rule of this module is that a command we cannot justify does not
exist. A fabricated verify command is worse than no verify command at all: it
fails on every run for a reason that has nothing to do with the model's edit
(``pytest: command not found``, ``no tests ran``), and after two or three of
those the model learns to treat verification as noise and stops reading it. So
``test`` is ``None`` unless something in the tree says tests are run that way,
and the absence is *reported* (see :attr:`VerifySpec.notes`) rather than papered
over with a plausible default.

Every command therefore carries the ``source`` file it came from and the
``evidence`` inside that file which justified it, so ``/doctor`` can answer "why
are you running this?" without re-deriving anything.

Precedence is the order in the work order: an explicit declaration first, then
``pyproject.toml``, ``package.json``, ``Makefile``, ``justfile``, ``Cargo.toml``,
``go.mod``. The first source that supplies a field wins and later sources only
fill gaps. The other reasonable order — a ``Makefile`` beating the ecosystem
file, on the theory that a human wrote the Makefile for humans — is noted in the
report; it is not built, because two precedence rules in one function is how a
detector becomes unexplainable.

This module reads files and imports nothing from Ronin. The declaration mapping
arrives as an argument precisely so that ``ronin.context`` (which owns RONIN.md
parsing) is not a dependency of ``verify``.
"""

from __future__ import annotations

import json
import shlex
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------- #
# Kinds and commands
# --------------------------------------------------------------------------- #


class CommandKind(StrEnum):
    """The four questions a verify pass can ask. Ordered cheapest-first."""

    LINT = "lint"
    TYPECHECK = "typecheck"
    TEST = "test"
    BUILD = "build"


#: The order a verify pass should run in: the cheap checks first, so a syntax
#: error is reported in a second rather than after a full suite.
KIND_ORDER: Final[tuple[CommandKind, ...]] = (
    CommandKind.LINT,
    CommandKind.TYPECHECK,
    CommandKind.TEST,
    CommandKind.BUILD,
)


@dataclass(frozen=True, slots=True)
class DetectedCommand:
    """One verify command, plus why we believe in it.

    ``argv`` is a real argument vector, never a shell string: these commands are
    run with ``exec``, so a repo whose directory name contains a space cannot
    turn into two arguments, and nothing in a declared command can smuggle a
    ``; rm -rf`` past the runner.
    """

    argv: tuple[str, ...]
    source: str
    evidence: str

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("DetectedCommand.argv must be non-empty")
        if any(not part for part in self.argv):
            raise ValueError(f"DetectedCommand.argv has an empty argument: {self.argv!r}")
        if not self.source:
            raise ValueError("DetectedCommand.source is required — /doctor has to name it")
        if not self.evidence:
            raise ValueError(
                "DetectedCommand.evidence is required — a command with no evidence "
                "is a guess, and this module does not ship guesses"
            )

    @property
    def display(self) -> str:
        """Shell-quoted form, for showing a human what will run."""
        return shlex.join(self.argv)

    def with_args(self, *extra: str) -> DetectedCommand:
        """Same command, narrowed to specific paths (``pytest`` → ``pytest a b``).

        Trailing ``.`` / ``./`` targets are dropped, because ``ruff check . a.py``
        checks the whole tree and silently defeats the point of scoping.
        """
        argv = list(self.argv)
        while argv and argv[-1] in {".", "./"}:
            argv.pop()
        return replace(self, argv=(*argv, *extra))


def parse_command(value: object) -> tuple[str, ...] | None:
    """Coerce a declared command into argv, or ``None`` if it is not one.

    Accepts a string (split with :mod:`shlex`, so quoting survives) or a list of
    strings (used verbatim). Anything else — a number, a nested mapping, a list
    with a non-string in it — is rejected rather than coerced, because a silently
    mangled command reappears later as an unexplainable verify failure.
    """
    if isinstance(value, str):
        parts = tuple(shlex.split(value))
        return parts or None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = tuple(str(item) for item in value if isinstance(item, str) and item)
        if len(parts) != len(list(value)):
            return None
        return parts or None
    return None


# --------------------------------------------------------------------------- #
# The spec
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VerifySpec:
    """The commands that decide whether this repo is in a good state.

    A field is ``None`` when the tree gave no evidence for it. ``notes`` carries
    the honest account of what was looked for and not found, which is the half of
    detection a user actually needs when verification is quieter than expected.
    """

    test: DetectedCommand | None = None
    lint: DetectedCommand | None = None
    typecheck: DetectedCommand | None = None
    build: DetectedCommand | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.notes, tuple):
            raise TypeError("VerifySpec.notes must be a tuple (frozen)")

    def get(self, kind: CommandKind) -> DetectedCommand | None:
        if kind is CommandKind.TEST:
            return self.test
        if kind is CommandKind.LINT:
            return self.lint
        if kind is CommandKind.TYPECHECK:
            return self.typecheck
        return self.build

    def with_command(self, kind: CommandKind, command: DetectedCommand) -> VerifySpec:
        # Spelled out rather than `replace(self, **{kind.value: ...})`: the dict
        # form type-checks as `Any` and would survive a renamed field.
        if kind is CommandKind.TEST:
            return replace(self, test=command)
        if kind is CommandKind.LINT:
            return replace(self, lint=command)
        if kind is CommandKind.TYPECHECK:
            return replace(self, typecheck=command)
        return replace(self, build=command)

    def with_note(self, note: str) -> VerifySpec:
        return replace(self, notes=(*self.notes, note))

    @property
    def available(self) -> tuple[CommandKind, ...]:
        return tuple(kind for kind in KIND_ORDER if self.get(kind) is not None)

    @property
    def missing(self) -> tuple[CommandKind, ...]:
        return tuple(kind for kind in KIND_ORDER if self.get(kind) is None)

    @property
    def empty(self) -> bool:
        """No command at all: verification cannot say anything about this repo."""
        return not self.available

    def explain(self) -> str:
        """The ``/doctor`` view: every kind, its command, and where it came from."""
        lines: list[str] = []
        for kind in KIND_ORDER:
            command = self.get(kind)
            if command is None:
                lines.append(f"{kind.value:<9} — not detected")
            else:
                lines.append(f"{kind.value:<9} {command.display}")
                lines.append(f"{'':<9}   from {command.source}: {command.evidence}")
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Interpreter / package-manager prefixes
# --------------------------------------------------------------------------- #

#: A lockfile means the project's tools live in a managed environment, so the
#: bare binary on PATH is the wrong one (or missing). Detecting the prefix from
#: the lockfile is still evidence-based — the file is right there.
_PYTHON_PREFIXES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("uv.lock", ("uv", "run")),
    ("poetry.lock", ("poetry", "run")),
)

_NODE_RUNNERS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("pnpm-lock.yaml", ("pnpm", "run")),
    ("yarn.lock", ("yarn", "run")),
    ("bun.lockb", ("bun", "run")),
    ("package-lock.json", ("npm", "run")),
)

#: Commands whose meaning does not survive having a path appended: they dispatch
#: to a script we cannot see, so ``make test tests/x.py`` asks make to build a
#: target named after a file. Scoping is refused for these, loudly.
INDIRECT_RUNNERS: Final[frozenset[str]] = frozenset(
    {"make", "just", "npm", "pnpm", "yarn", "bun", "npx", "poe", "task", "nox", "tox"}
)


def _python_prefix(root: Path) -> tuple[tuple[str, ...], str]:
    for name, prefix in _PYTHON_PREFIXES:
        if (root / name).is_file():
            return prefix, f"{name} present, so tools run through `{' '.join(prefix)}`"
    return (), ""


def _node_runner(root: Path) -> tuple[tuple[str, ...], str]:
    for name, prefix in _NODE_RUNNERS:
        if (root / name).is_file():
            return prefix, f"{name} present"
    return ("npm", "run"), "package.json with no lockfile; npm is node's default"


def is_scopable(command: DetectedCommand) -> bool:
    """Whether appending paths to this command narrows it or breaks it.

    ``pytest`` narrows. ``make test`` breaks — and a broken narrowing shows up as
    a verify failure the model will try to fix in its own code, which is the
    worst possible outcome of a scoping bug.
    """
    argv = list(command.argv)
    while len(argv) >= 2 and argv[0] in {"uv", "poetry", "pdm", "hatch"} and argv[1] == "run":
        argv = argv[2:]
    return bool(argv) and Path(argv[0]).name not in INDIRECT_RUNNERS


# --------------------------------------------------------------------------- #
# Declaration (RONIN.md, already parsed by someone else)
# --------------------------------------------------------------------------- #

_DECLARED_KEYS: Final[Mapping[str, CommandKind]] = {
    "test": CommandKind.TEST,
    "tests": CommandKind.TEST,
    "lint": CommandKind.LINT,
    "typecheck": CommandKind.TYPECHECK,
    "type_check": CommandKind.TYPECHECK,
    "type-check": CommandKind.TYPECHECK,
    "build": CommandKind.BUILD,
}


def from_declaration(declared: Mapping[str, object], *, source: str = "RONIN.md") -> VerifySpec:
    """Read an already-parsed declaration mapping into a spec.

    Accepts either a flat mapping (``{"test": "pytest -q"}``) or one nested under
    a ``verify`` key, because both shapes show up in a hand-written RONIN.md. An
    unusable value is dropped *with a note* rather than guessed at.
    """
    body: Mapping[str, object] = declared
    nested = declared.get("verify")
    if isinstance(nested, Mapping):
        body = {**{k: v for k, v in declared.items() if k != "verify"}, **nested}

    spec = VerifySpec()
    for key, value in body.items():
        kind = _DECLARED_KEYS.get(key.strip().lower().replace(" ", "_"))
        if kind is None:
            continue
        argv = parse_command(value)
        if argv is None:
            spec = spec.with_note(
                f"{source} declares {key!r} but the value is not a command "
                f"({type(value).__name__}); ignored"
            )
            continue
        spec = spec.with_command(
            kind,
            DetectedCommand(argv=argv, source=source, evidence=f"declared explicitly as {key!r}"),
        )
    return spec


# --------------------------------------------------------------------------- #
# Inference, one source at a time
# --------------------------------------------------------------------------- #


def _load_toml(path: Path) -> Mapping[str, object] | None:
    try:
        with path.open("rb") as handle:
            loaded = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return loaded


def _table(parent: Mapping[str, object], *names: str) -> Mapping[str, object] | None:
    node: object = parent
    for name in names:
        if not isinstance(node, Mapping):
            return None
        node = node.get(name)
    return node if isinstance(node, Mapping) else None


def _declares_dependency(data: Mapping[str, object], needle: str) -> bool:
    """Whether ``needle`` appears in any dependency list in a pyproject mapping.

    Walks the whole document rather than enumerating the six places a dev
    dependency can live (``project.dependencies``, ``optional-dependencies.*``,
    ``dependency-groups.*``, ``tool.poetry.group.*.dependencies``,
    ``tool.pdm.dev-dependencies``, ``tool.hatch.envs.*.dependencies``); an
    enumeration of those goes stale, and a false *positive* here only costs us a
    command the repo does in fact declare somewhere.
    """
    stack: list[object] = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            stack.extend(node.values())
        elif isinstance(node, str):
            token = node.strip().lower()
            if token == needle or token.startswith(
                (f"{needle}>", f"{needle}=", f"{needle}[", f"{needle}<", f"{needle}~", f"{needle} ")
            ):
                return True
        elif isinstance(node, Sequence) and not isinstance(node, (bytes, bytearray)):
            stack.extend(node)
    return False


def _from_pyproject(root: Path) -> VerifySpec:
    path = root / "pyproject.toml"
    if not path.is_file():
        return VerifySpec()
    data = _load_toml(path)
    if data is None:
        return VerifySpec().with_note("pyproject.toml exists but could not be parsed as TOML")

    prefix, prefix_evidence = _python_prefix(root)
    source = "pyproject.toml"

    def command(argv: Sequence[str], evidence: str) -> DetectedCommand:
        full = (*prefix, *argv)
        detail = f"{evidence}; {prefix_evidence}" if prefix_evidence else evidence
        return DetectedCommand(argv=full, source=source, evidence=detail)

    spec = VerifySpec()

    if _table(data, "tool", "pytest", "ini_options") is not None:
        spec = spec.with_command(
            CommandKind.TEST, command(("pytest",), "[tool.pytest.ini_options] configures pytest")
        )
    elif _declares_dependency(data, "pytest"):
        spec = spec.with_command(
            CommandKind.TEST, command(("pytest",), "pytest is a declared dependency")
        )
    else:
        spec = spec.with_note(
            "pyproject.toml has no pytest config and does not depend on pytest, so no "
            "test command was inferred from it"
        )

    if _table(data, "tool", "ruff") is not None:
        spec = spec.with_command(
            CommandKind.LINT, command(("ruff", "check", "."), "[tool.ruff] is configured")
        )
    if _table(data, "tool", "mypy") is not None:
        spec = spec.with_command(
            CommandKind.TYPECHECK, command(("mypy",), "[tool.mypy] is configured")
        )
    elif _table(data, "tool", "pyright") is not None:
        spec = spec.with_command(
            CommandKind.TYPECHECK, command(("pyright",), "[tool.pyright] is configured")
        )

    if _declares_dependency(data, "build"):
        spec = spec.with_command(
            CommandKind.BUILD,
            command(("python", "-m", "build"), "`build` is a declared dependency"),
        )
    elif _table(data, "build-system") is not None:
        spec = spec.with_note(
            "pyproject.toml has a [build-system] but does not depend on `build`, so no "
            "build command was inferred (`python -m build` would fail on a bare install)"
        )
    return spec


_PACKAGE_SCRIPTS: Final[Mapping[CommandKind, tuple[str, ...]]] = {
    CommandKind.TEST: ("test",),
    CommandKind.LINT: ("lint",),
    CommandKind.TYPECHECK: ("typecheck", "type-check", "types", "tsc"),
    CommandKind.BUILD: ("build",),
}


def _from_package_json(root: Path) -> VerifySpec:
    path = root / "package.json"
    if not path.is_file():
        return VerifySpec()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return VerifySpec().with_note("package.json exists but could not be parsed as JSON")
    scripts = data.get("scripts") if isinstance(data, Mapping) else None
    if not isinstance(scripts, Mapping):
        return VerifySpec().with_note("package.json has no scripts block")

    runner, runner_evidence = _node_runner(root)
    spec = VerifySpec()
    for kind, names in _PACKAGE_SCRIPTS.items():
        for name in names:
            if isinstance(scripts.get(name), str):
                spec = spec.with_command(
                    kind,
                    DetectedCommand(
                        argv=(*runner, name),
                        source="package.json",
                        evidence=f"scripts.{name} is defined; {runner_evidence}",
                    ),
                )
                break
    return spec


_MAKE_TARGETS: Final[Mapping[CommandKind, tuple[str, ...]]] = {
    CommandKind.TEST: ("test", "tests", "pytest"),
    CommandKind.LINT: ("lint", "ruff", "flake8"),
    CommandKind.TYPECHECK: ("typecheck", "type-check", "types", "mypy"),
    CommandKind.BUILD: ("build", "dist"),
}


def _recipe_names(text: str) -> tuple[str, ...]:
    """Target/recipe names declared at column zero.

    Column zero matters: an indented line in a Makefile is a *command* inside a
    recipe, and a recipe line like ``curl http://host:8080`` looks exactly like a
    target definition to a regex that does not care about indentation.
    """
    names: list[str] = []
    for raw in text.splitlines():
        if not raw or raw[0].isspace() or raw.lstrip().startswith("#"):
            continue
        head, sep, rest = raw.partition(":")
        if not sep or head.startswith("."):
            continue
        if rest.startswith("="):
            continue  # `VAR := value` is an assignment, not a target
        parts = head.split()
        name = parts[0] if parts else ""
        if name and all(char.isalnum() or char in "_-." for char in name):
            names.append(name)
    return tuple(names)


def _from_recipe_file(root: Path, filename: str, runner: str) -> VerifySpec:
    path = root / filename
    if not path.is_file():
        return VerifySpec()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return VerifySpec()
    names = _recipe_names(text)
    word = "target" if runner == "make" else "recipe"
    spec = VerifySpec()
    for kind, wanted in _MAKE_TARGETS.items():
        for name in wanted:
            if name in names:
                spec = spec.with_command(
                    kind,
                    DetectedCommand(
                        argv=(runner, name),
                        source=filename,
                        evidence=f"a `{name}` {word} is declared",
                    ),
                )
                break
    return spec


def _from_cargo(root: Path) -> VerifySpec:
    if not (root / "Cargo.toml").is_file():
        return VerifySpec()
    source = "Cargo.toml"
    evidence = "cargo ships this subcommand, so it exists wherever Cargo.toml does"
    spec = VerifySpec(
        test=DetectedCommand(argv=("cargo", "test"), source=source, evidence=evidence),
        typecheck=DetectedCommand(argv=("cargo", "check"), source=source, evidence=evidence),
        build=DetectedCommand(argv=("cargo", "build"), source=source, evidence=evidence),
    )
    return spec.with_note(
        "no Rust lint command inferred: clippy is a separate toolchain component and "
        "`cargo clippy` fails with `no such subcommand` when it is not installed"
    )


def _from_go_mod(root: Path) -> VerifySpec:
    if not (root / "go.mod").is_file():
        return VerifySpec()
    source = "go.mod"
    evidence = "part of the go toolchain, so it exists wherever go.mod does"
    return VerifySpec(
        test=DetectedCommand(argv=("go", "test", "./..."), source=source, evidence=evidence),
        lint=DetectedCommand(argv=("go", "vet", "./..."), source=source, evidence=evidence),
        build=DetectedCommand(argv=("go", "build", "./..."), source=source, evidence=evidence),
    ).with_note(
        "no separate Go typecheck command: `go build` is the type checker, and it is "
        "already the build command"
    )


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def _merge(base: VerifySpec, addition: VerifySpec) -> VerifySpec:
    """Fill only the gaps in ``base``; notes always accumulate."""
    merged = base
    for kind in KIND_ORDER:
        candidate = addition.get(kind)
        if candidate is not None and merged.get(kind) is None:
            merged = merged.with_command(kind, candidate)
    for note in addition.notes:
        merged = merged.with_note(note)
    return merged


def detect_verify_spec(
    root: Path,
    *,
    declared: Mapping[str, object] | None = None,
) -> VerifySpec:
    """Detect this repo's verify commands, in the documented precedence order.

    ``declared`` is an already-parsed mapping (RONIN.md's verify declaration).
    Parsing RONIN.md belongs to ``ronin.context``; taking the mapping as an
    argument is what keeps ``verify`` dependent on ``core`` alone.
    """
    spec = from_declaration(declared) if declared else VerifySpec()
    for detector in (
        _from_pyproject,
        _from_package_json,
        lambda base: _from_recipe_file(base, "Makefile", "make"),
        lambda base: _from_recipe_file(base, "justfile", "just"),
        _from_cargo,
        _from_go_mod,
    ):
        spec = _merge(spec, detector(root))

    if spec.empty:
        spec = spec.with_note(
            "no verify command could be justified from this tree; verification will "
            "report 'unknown' rather than invent a command that always fails"
        )
    return spec


__all__ = [
    "INDIRECT_RUNNERS",
    "KIND_ORDER",
    "CommandKind",
    "DetectedCommand",
    "VerifySpec",
    "detect_verify_spec",
    "from_declaration",
    "is_scopable",
    "parse_command",
]
