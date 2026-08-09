"""Slash commands: parsing, validation, and the registry. Stdlib only.

This module **defines and validates**; it never executes. ``parse`` returns a
structured intent (:class:`ParsedCommand`) or a structured refusal
(:class:`ParseError`), and the orchestrator decides what ``/undo`` means. Keeping
dispatch out means the parser is testable as a table and the same intent can be
served by the TUI, the headless runner, or an SDK caller.

Two failure modes drive the design:

- **A typo must suggest.** ``/moddel`` answering only "unknown command" is the most
  annoying possible failure, so an unknown name is matched against the known set
  with ``difflib.get_close_matches``.
- **A user-defined command must fail loudly and name its file.** A template that
  references ``$3`` when the user supplied two arguments is a bug in *that file*;
  substituting an empty string turns it into a mysteriously wrong prompt sent to a
  model. The error names the path.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# --------------------------------------------------------------------------- #
# Command declarations
# --------------------------------------------------------------------------- #

PREFIX = "/"
#: Where user-defined commands live, relative to the project root.
USER_COMMAND_DIRECTORY = ".ronin/commands"
USER_COMMAND_SUFFIX = ".md"
#: A command name is a bare word: no spaces, no path separators, no leading dash.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SUMMARY_LIMIT = 120
#: How close a typo has to be before we suggest a name.
SUGGESTION_CUTOFF = 0.6
SUGGESTION_COUNT = 3


class ArgSpec(StrEnum):
    """What a command does with the text after its name."""

    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class Command:
    """One command's declaration. Data only — no handler, no side effect."""

    name: str
    summary: str
    arg_spec: ArgSpec = ArgSpec.NONE

    def __post_init__(self) -> None:
        if not NAME_PATTERN.match(self.name):
            raise ValueError(
                f"command name {self.name!r} must match {NAME_PATTERN.pattern} — "
                "a name with a space or a path separator cannot be typed as a slash "
                "command"
            )
        if not self.summary:
            raise ValueError(
                f"command {self.name!r} needs a summary — /help is the only place a "
                "user learns it exists"
            )

    @property
    def usage(self) -> str:
        if self.arg_spec is ArgSpec.REQUIRED:
            return f"{PREFIX}{self.name} <arguments>"
        if self.arg_spec is ArgSpec.OPTIONAL:
            return f"{PREFIX}{self.name} [arguments]"
        return f"{PREFIX}{self.name}"


#: The built-ins, in the order ``/help`` lists them.
BUILTIN_COMMANDS: tuple[Command, ...] = (
    Command("help", "list commands, or explain one", ArgSpec.OPTIONAL),
    Command("clear", "drop the transcript and start a fresh session"),
    Command("compact", "summarize the transcript to reclaim context", ArgSpec.OPTIONAL),
    Command("undo", "revert the last applied change", ArgSpec.OPTIONAL),
    Command("diff", "show pending changes, all of them or one path", ArgSpec.OPTIONAL),
    Command("model", "show the current model, or switch to another", ArgSpec.OPTIONAL),
    Command("cost", "show token and dollar spend for this session"),
    Command("plan", "switch to plan mode: read and reason, mutate nothing"),
    Command("resume", "resume a saved session", ArgSpec.OPTIONAL),
    Command("agents", "list subagents, or inspect one", ArgSpec.OPTIONAL),
    Command("hooks", "show configured hooks"),
    Command("init", "write a RONIN.md describing this repository"),
    Command("doctor", "check the install: providers, tools, permissions"),
)


# --------------------------------------------------------------------------- #
# Parse results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """A validated intent. The orchestrator dispatches on ``command.name``.

    ``body`` is populated only for user-defined commands: it is the template with
    substitutions applied, i.e. the prompt to send. A built-in has no body because
    its behaviour is code, not text.
    """

    command: Command
    argument: str = ""
    args: tuple[str, ...] = ()
    body: str = ""
    source: str = ""

    @property
    def name(self) -> str:
        return self.command.name

    @property
    def is_user_defined(self) -> bool:
        return bool(self.source)


@dataclass(frozen=True, slots=True)
class ParseError:
    """Why a line is not a runnable command. ``message`` is shown to the user."""

    message: str
    suggestion: str = ""

    @property
    def display(self) -> str:
        if self.suggestion:
            return f"{self.message} (did you mean {PREFIX}{self.suggestion}?)"
        return self.message


@dataclass(frozen=True, slots=True)
class UserCommand:
    """A command loaded from ``.ronin/commands/<name>.md``."""

    command: Command
    path: str
    template: str


@dataclass(frozen=True, slots=True)
class Registry:
    """The command set in effect: built-ins plus whatever the project defines.

    A user file may shadow a built-in name; the built-in wins. Letting a dropped-in
    markdown file take over ``/clear`` would make an ordinary command silently mean
    something else.
    """

    builtins: tuple[Command, ...] = BUILTIN_COMMANDS
    user: tuple[UserCommand, ...] = ()

    def names(self) -> tuple[str, ...]:
        seen = {command.name for command in self.builtins}
        extra = tuple(
            entry.command.name for entry in self.user if entry.command.name not in seen
        )
        return tuple(command.name for command in self.builtins) + extra

    def get(self, name: str) -> Command | None:
        for command in self.builtins:
            if command.name == name:
                return command
        entry = self.user_command(name)
        return entry.command if entry is not None else None

    def user_command(self, name: str) -> UserCommand | None:
        if any(command.name == name for command in self.builtins):
            return None
        for entry in self.user:
            if entry.command.name == name:
                return entry
        return None

    def with_user(self, user: Sequence[UserCommand]) -> Registry:
        return Registry(builtins=self.builtins, user=tuple(user))


#: The default registry: built-ins only, no filesystem touched.
BUILTIN_REGISTRY = Registry()


# --------------------------------------------------------------------------- #
# Substitution
# --------------------------------------------------------------------------- #

#: ``$$`` first so an escaped dollar can never start a placeholder.
_PLACEHOLDER = re.compile(r"\$\$|\$ARGUMENTS\b|\$(\d+)")
ESCAPED_DOLLAR = "$$"
ARGUMENTS_PLACEHOLDER = "$ARGUMENTS"


@dataclass(frozen=True, slots=True)
class Substitution:
    """The outcome of expanding one template. ``missing`` empty means success."""

    text: str
    missing: tuple[str, ...] = ()


def substitute(template: str, argument: str, args: Sequence[str]) -> Substitution:
    """Expand ``$ARGUMENTS``, ``$1``…``$n`` and the ``$$`` escape, in one pass.

    One left-to-right pass is the point: ``$$1`` is a literal ``$1``, not the first
    positional, and a two-pass implementation gets that wrong. Anything else that
    starts with ``$`` (``$HOME``, ``$@``) is left verbatim — a prompt is allowed to
    talk about shell variables.

    Missing positionals are *reported*, never filled with an empty string.
    """
    missing: list[str] = []

    def _expand(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == ESCAPED_DOLLAR:
            return "$"
        if token == ARGUMENTS_PLACEHOLDER:
            return argument
        index = int(match.group(1))
        if index < 1 or index > len(args):
            missing.append(token)
            return token
        return args[index - 1]

    return Substitution(text=_PLACEHOLDER.sub(_expand, template), missing=tuple(missing))


def placeholders(template: str) -> tuple[str, ...]:
    """Every placeholder a template actually uses, in order of appearance."""
    found: list[str] = []
    for match in _PLACEHOLDER.finditer(template):
        token = match.group(0)
        if token != ESCAPED_DOLLAR and token not in found:
            found.append(token)
    return tuple(found)


def arg_spec_for_template(template: str) -> ArgSpec:
    """A template that reads ``$1`` needs an argument; one that reads ``$ARGUMENTS`` may."""
    used = placeholders(template)
    if any(token != ARGUMENTS_PLACEHOLDER for token in used):
        return ArgSpec.REQUIRED
    if ARGUMENTS_PLACEHOLDER in used:
        return ArgSpec.OPTIONAL
    return ArgSpec.NONE


# --------------------------------------------------------------------------- #
# Loading user commands
# --------------------------------------------------------------------------- #

DEFAULT_USER_SUMMARY = "user-defined command"


def _summary_from(template: str, name: str) -> str:
    for raw in template.split("\n"):
        line = raw.strip().lstrip("#").strip()
        if line:
            return line[:SUMMARY_LIMIT]
    return f"{DEFAULT_USER_SUMMARY} ({name})"


def load_user_commands(directory: str | Path) -> tuple[UserCommand, ...]:
    """Load ``*.md`` from ``directory``, sorted by name. A missing directory is empty.

    A file whose stem is not a legal command name is skipped rather than raised on:
    one badly named file in a shared repo should not stop the UI from starting. The
    skip is silent here and surfaced by ``/help`` listing what it did load — the
    honest alternative (raising) would make an unrelated file fatal.
    """
    root = Path(directory)
    if not root.is_dir():
        return ()
    loaded: list[UserCommand] = []
    for path in sorted(root.glob(f"*{USER_COMMAND_SUFFIX}")):
        name = path.stem
        if not NAME_PATTERN.match(name):
            continue
        template = path.read_text(encoding="utf-8")
        loaded.append(
            UserCommand(
                command=Command(
                    name=name,
                    summary=_summary_from(template, name),
                    arg_spec=arg_spec_for_template(template),
                ),
                path=str(path),
                template=template,
            )
        )
    return tuple(loaded)


def load_registry(project_root: str | Path) -> Registry:
    """Built-ins plus ``<project_root>/.ronin/commands/*.md``."""
    return BUILTIN_REGISTRY.with_user(
        load_user_commands(Path(project_root) / USER_COMMAND_DIRECTORY)
    )


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def is_command(line: str) -> bool:
    """Whether ``line`` is addressed to the command layer at all.

    ``/`` alone, and ``/ something``, are not: a lone slash is a typo and a path
    like ``/usr/bin`` typed as a prompt should reach the model.
    """
    stripped = line.strip()
    if not stripped.startswith(PREFIX) or len(stripped) == 1:
        return False
    return bool(NAME_PATTERN.match(stripped[1:].split(maxsplit=1)[0].lower()))


def suggest(name: str, known: Sequence[str]) -> str:
    """The closest known command name, or ``""`` when nothing is close."""
    import difflib

    matches = difflib.get_close_matches(
        name, list(known), n=SUGGESTION_COUNT, cutoff=SUGGESTION_COUNT and SUGGESTION_CUTOFF
    )
    return matches[0] if matches else ""


def split_argument(argument: str) -> tuple[tuple[str, ...], str]:
    """Split an argument string shell-style. Returns ``(args, error)``.

    ``shlex`` is used so ``/review "two words" second`` has two arguments rather
    than three, and an unbalanced quote becomes a message instead of an exception.
    """
    if not argument:
        return (), ""
    try:
        return tuple(shlex.split(argument)), ""
    except ValueError as exc:
        return (), f"cannot split arguments: {exc}"


def parse(line: str, *, registry: Registry = BUILTIN_REGISTRY) -> ParsedCommand | ParseError:
    """The single front door. Returns an intent or a refusal, never raises."""
    stripped = line.strip()
    if not stripped.startswith(PREFIX):
        return ParseError(f"not a slash command: {line!r} does not start with {PREFIX!r}")
    head, _, rest = stripped[1:].partition(" ")
    name = head.lower()
    if not name:
        return ParseError(f"{PREFIX!r} is not a command; type {PREFIX}help for the list")
    if not NAME_PATTERN.match(name):
        return ParseError(
            f"{PREFIX}{head} is not a command name (expected {NAME_PATTERN.pattern})"
        )

    command = registry.get(name)
    if command is None:
        return ParseError(
            f"unknown command {PREFIX}{name}", suggestion=suggest(name, registry.names())
        )

    argument = rest.strip()
    args, split_error = split_argument(argument)
    if split_error:
        return ParseError(f"{PREFIX}{name}: {split_error}")

    if command.arg_spec is ArgSpec.NONE and argument:
        return ParseError(
            f"{PREFIX}{name} takes no arguments; got {argument!r}. usage: {command.usage}"
        )
    if command.arg_spec is ArgSpec.REQUIRED and not argument:
        return ParseError(f"{PREFIX}{name} needs an argument. usage: {command.usage}")

    entry = registry.user_command(name)
    if entry is None:
        return ParsedCommand(command=command, argument=argument, args=args)

    expanded = substitute(entry.template, argument, args)
    if expanded.missing:
        return ParseError(
            f"{entry.path} references {', '.join(expanded.missing)} but only "
            f"{len(args)} argument(s) were given. usage: {command.usage}"
        )
    return ParsedCommand(
        command=command,
        argument=argument,
        args=args,
        body=expanded.text,
        source=entry.path,
    )


def render_help(registry: Registry = BUILTIN_REGISTRY) -> str:
    """The ``/help`` body, built from the registry so it can never drift."""
    entries: list[tuple[str, str]] = [
        (command.usage, command.summary) for command in registry.builtins
    ]
    entries += [
        (entry.command.usage, f"{entry.command.summary}  [{entry.path}]")
        for entry in registry.user
        if registry.user_command(entry.command.name) is not None
    ]
    width = max((len(usage) for usage, _ in entries), default=0)
    return "\n".join(f"{usage.ljust(width)}  {summary}" for usage, summary in entries)


HELP_TOPICS: Mapping[str, str] = {
    "modes": "shift+tab cycles normal → auto-accept → plan.",
    "keys": "esc interrupts; esc esc rewinds to an earlier turn.",
}


__all__ = [
    "ARGUMENTS_PLACEHOLDER",
    "BUILTIN_COMMANDS",
    "BUILTIN_REGISTRY",
    "DEFAULT_USER_SUMMARY",
    "ESCAPED_DOLLAR",
    "HELP_TOPICS",
    "NAME_PATTERN",
    "PREFIX",
    "SUGGESTION_CUTOFF",
    "USER_COMMAND_DIRECTORY",
    "ArgSpec",
    "Command",
    "ParseError",
    "ParsedCommand",
    "Registry",
    "Substitution",
    "UserCommand",
    "arg_spec_for_template",
    "is_command",
    "load_registry",
    "load_user_commands",
    "parse",
    "placeholders",
    "render_help",
    "split_argument",
    "substitute",
    "suggest",
]
