"""The policy engine: rules, precedence, and the four ways a human can answer.

The governing constraint for every decision in this module is that **the gate has to be
cheap to say yes to**. A gate that prompts on ``ls -la`` gets switched off inside a day,
and a switched-off gate protects nothing at all, so breadth of allowlist is a safety
feature and not a concession.

Precedence — the whole rule, stated once
----------------------------------------

A rule is ``(tool, matcher, decision)``. Resolving one tool call:

1. Collect every rule whose ``tool`` matches (exact name, or ``*``) and whose matcher
   matches. Non-matching rules are irrelevant, however alarming they look.
2. **The most specific matcher wins outright.** Specificity is
   ``Exact`` (3) > ``PathGlob`` = ``CommandRegex`` (2) > ``AnyUse`` (0), tie-broken by
   an exact tool name beating ``*``. A specific ``allow`` therefore beats a tool-wide
   ``deny``, and that is deliberate: it is the only way "gate ``bash``, except these
   twelve commands" can be expressed, which is the configuration everybody actually
   wants. The unconditional floor is :mod:`ronin.safety.denylist`, *not* a tool-wide
   deny rule — no rule at any specificity can lift a deny-list entry.
3. **At equal specificity the most restrictive decision wins**: deny beats ask beats
   allow. Two rules that tie on both specificity and decision are the same answer; the
   later one (later config layer) is the one reported, so provenance points at the file
   a user most recently edited.
4. If nothing matched, the ruleset's ``default`` applies (``ask``).

Commands are resolved **per segment**, and every segment that matched no rule
contributes the default. That is what stops ``echo safe; rm -rf /`` from walking through
an allowlist that permits ``echo``: the second segment matches nothing, contributes
``ask``, and the deny list turns it into ``deny``.

Four floors sit on top of the rules and can only make the answer stricter:

* the **deny list** (unconditional; only ``--yolo`` removes it),
* **structural hazards** from :func:`ronin.safety.command.hazards`,
* **taint** — anything derived from freshly fetched untrusted content is at least
  ``ask``, whatever the allowlist says, and that escalation cannot be remembered,
* **plan mode**, which denies anything that mutates.

``Mode.FULL`` and ``Mode.AUTO_EDIT`` may only *relax* an ``ask`` that came from the
rules. They cannot touch a floor. A mode that could waive the deny list would make the
deny list a suggestion.

Saying no is a first-class answer
---------------------------------

:class:`Outcome` has four members, not a boolean: yes once, yes for the session, yes and
write it down, and **no with feedback**. The last one is the one that makes the whole
gate usable — a human who has to choose between "yes" and "kill the turn" will pick yes.
Feedback travels verbatim into ``ApprovalDecision.reason``, which the loop feeds back as
a tool result, so "no, use the staging database instead" is something the model can act
on rather than a dead end.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Protocol, runtime_checkable

from ..core.types import (
    ApprovalDecision,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Mode,
    ToolSpec,
    ToolUse,
)
from .command import Hazard, Segment, Severity, hazards, parse_command
from .denylist import DenyHit, Denylist
from .injection import TaintHit, TaintTracker
from .sandbox import SANDBOX_AUTO_APPROVES, Sandbox

# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


class Decision(StrEnum):
    """What policy says about one call, before a human is involved."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]


_SEVERITY: Mapping[Decision, int] = {Decision.ALLOW: 0, Decision.ASK: 1, Decision.DENY: 2}


def most_restrictive(decisions: Sequence[Decision], *, default: Decision) -> Decision:
    """The strictest of ``decisions``, or ``default`` when there are none."""
    if not decisions:
        return default
    return max(decisions, key=lambda decision: decision.severity)


# --------------------------------------------------------------------------- #
# Matchers
# --------------------------------------------------------------------------- #

#: Argument names that hold a filesystem path. Used when a tool is not command-shaped.
PATH_ARGUMENTS: frozenset[str] = frozenset(
    {
        "path",
        "file_path",
        "paths",
        "target",
        "directory",
        "dir",
        "notebook_path",
        "old_path",
        "new_path",
        "source",
        "destination",
    }
)

#: The argument a shell-style tool puts its command in.
COMMAND_ARGUMENT = "command"


@dataclass(frozen=True, slots=True)
class MatchTarget:
    """What a matcher is asked about: one tool call, optionally narrowed to a segment.

    When ``segment`` is set, ``command_text`` is that segment's *resolved* command line
    rather than the raw string. Matching the resolution is what makes a rule written as
    ``^git status`` survive ``env GIT_PAGER=cat /usr/bin/git status`` and refuse to be
    fooled by ``r''m``.
    """

    tool: str
    arguments: Mapping[str, Any]
    segment: Segment | None = None
    command_field: str = COMMAND_ARGUMENT

    @property
    def command_text(self) -> str:
        if self.segment is not None:
            return self.segment.line
        value = self.arguments.get(self.command_field)
        return value if isinstance(value, str) else ""

    def argument(self, name: str) -> str | None:
        """One argument as text, with the command field routed through the segment."""
        if name == self.command_field and (self.segment is not None or self.command_text):
            return self.command_text
        value = self.arguments.get(name)
        return value if isinstance(value, str) else None

    def path_values(self) -> tuple[str, ...]:
        if self.segment is not None:
            return self.segment.path_words
        out: list[str] = []
        for key, value in self.arguments.items():
            if key not in PATH_ARGUMENTS:
                continue
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                out.extend(item for item in value if isinstance(item, str))
        return tuple(out)


@dataclass(frozen=True, slots=True)
class AnyUse:
    """Tool-wide: matches every call to the tool. The least specific matcher."""

    kind: ClassVar[str] = "tool"
    specificity: ClassVar[int] = 0

    def matches(self, target: MatchTarget) -> bool:
        return True

    def describe(self) -> str:
        return "any use of this tool"


@dataclass(frozen=True, slots=True)
class Exact:
    """One argument equal to one value, byte for byte. The most specific matcher."""

    argument: str
    value: str
    kind: ClassVar[str] = "exact"
    specificity: ClassVar[int] = 3

    def matches(self, target: MatchTarget) -> bool:
        return target.argument(self.argument) == self.value

    def describe(self) -> str:
        return f"{self.argument} == {self.value!r}"


@dataclass(frozen=True, slots=True)
class PathGlob:
    """A glob over path-shaped arguments.

    ``*`` stops at a path separator and ``**`` crosses it, as in every other tool a
    developer already knows. ``fnmatch`` was the obvious shortcut and is wrong for
    exactly this: its ``*`` crosses separators, so ``src/*`` would silently cover
    ``src/a/b/c.py`` and an allow rule would be broader than it reads.
    """

    pattern: str
    argument: str = "*"
    kind: ClassVar[str] = "path"
    specificity: ClassVar[int] = 2

    def matches(self, target: MatchTarget) -> bool:
        candidates = (
            target.path_values()
            if self.argument == "*"
            else tuple(v for v in (target.argument(self.argument),) if v is not None)
        )
        return any(re.match(glob_to_regex(self.pattern), value) for value in candidates)

    def describe(self) -> str:
        where = "any path argument" if self.argument == "*" else self.argument
        return f"{where} matching {self.pattern!r}"


@dataclass(frozen=True, slots=True)
class CommandRegex:
    """A regex over the resolved command line. Anchor it yourself (``^…``).

    Uses :func:`re.search`, so an unanchored pattern matches anywhere — which is the
    right default for a deny rule and a footgun for an allow rule. The builtin allow
    rules are all anchored, and that is why.
    """

    pattern: str
    kind: ClassVar[str] = "regex"
    specificity: ClassVar[int] = 2

    def __post_init__(self) -> None:
        # Fail where the rule is written, not on the call it was meant to gate.
        re.compile(self.pattern)

    def matches(self, target: MatchTarget) -> bool:
        text = target.command_text
        return bool(text) and re.search(self.pattern, text) is not None

    def describe(self) -> str:
        return f"command matching /{self.pattern}/"


#: The closed set of matchers. Anything else is a bug, not an extension point — a
#: stringly-typed matcher would move rule validation from load time to decision time.
Matcher = AnyUse | Exact | PathGlob | CommandRegex

MATCHER_KINDS: Mapping[str, type[AnyUse] | type[Exact] | type[PathGlob] | type[CommandRegex]] = {
    AnyUse.kind: AnyUse,
    Exact.kind: Exact,
    PathGlob.kind: PathGlob,
    CommandRegex.kind: CommandRegex,
}


def glob_to_regex(pattern: str) -> str:
    """Translate a path glob to a regex where ``**`` crosses ``/`` and ``*`` does not."""
    out = ["(?s:"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**", index):
                index += 2
                if pattern.startswith("/", index):
                    # `**/x` should also match a bare `x`.
                    out.append("(?:.*/)?")
                    index += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    out.append(r")\Z")
    return "".join(out)


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #

#: Layer name for rules compiled into the binary rather than read from a file.
BUILTIN_SOURCE = "builtin"


@dataclass(frozen=True, slots=True)
class Rule:
    """One ``(tool, matcher, decision)`` triple, with where it came from.

    ``source`` is provenance and it is not decoration: a user who cannot see which file
    granted a permission has no way to revoke it, and someone who cannot revoke a
    permission ends up deleting the config and turning the gate off.

    ``unwaivable`` marks a gate that must be asked every time. It makes
    ``ApprovalDecision.remember`` a request rather than an authorization: the human can
    still say yes, but the yes is not written down.
    """

    tool: str
    matcher: Matcher
    decision: Decision
    source: str = BUILTIN_SOURCE
    reason: str = ""
    unwaivable: bool = False

    def __post_init__(self) -> None:
        if not self.tool:
            raise ValueError("Rule.tool is required ('*' for any tool)")
        if self.unwaivable and self.decision is Decision.ALLOW:
            raise ValueError(
                "an allow rule cannot be unwaivable — there is nothing to waive; "
                "unwaivable belongs on the ask/deny rule that must keep gating"
            )

    @property
    def specificity(self) -> tuple[int, int]:
        return (self.matcher.specificity, 0 if self.tool == "*" else 1)

    def applies_to(self, tool: str) -> bool:
        return self.tool in {"*", tool}

    def matches(self, target: MatchTarget) -> bool:
        return self.applies_to(target.tool) and self.matcher.matches(target)

    def describe(self) -> str:
        tail = f" ({self.reason})" if self.reason else ""
        gate = " [always asked]" if self.unwaivable else ""
        return (
            f"{self.decision.value} {self.tool} where {self.matcher.describe()} "
            f"— from {self.source}{tail}{gate}"
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    """The ruleset's answer for one target, with everything needed to explain it."""

    decision: Decision
    rule: Rule | None
    matched: tuple[Rule, ...] = ()
    subject: str = ""

    @property
    def source(self) -> str:
        return self.rule.source if self.rule is not None else "default"

    def explain(self) -> str:
        head = f"{self.decision.value}"
        if self.subject:
            head = f"{head} `{self.subject}`"
        if self.rule is None:
            return f"{head}: no rule matched, so the ruleset default applied"
        losers = [r for r in self.matched if r is not self.rule]
        text = f"{head}: {self.rule.describe()}"
        if losers:
            text += f"; outranked {len(losers)} other rule(s): " + "; ".join(
                r.describe() for r in losers
            )
        return text


@dataclass(frozen=True, slots=True)
class RuleSet:
    """An ordered, immutable rule list plus the answer when none of them match."""

    rules: tuple[Rule, ...] = ()
    default: Decision = Decision.ASK

    def with_rules(self, extra: Sequence[Rule]) -> RuleSet:
        """A new ruleset with ``extra`` appended. Later rules report as provenance."""
        return RuleSet(rules=(*self.rules, *extra), default=self.default)

    def for_tool(self, tool: str) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.applies_to(tool))

    def resolve(self, target: MatchTarget) -> Resolution:
        """Apply the precedence rule documented in this module's docstring."""
        subject = target.command_text or target.tool
        matched = tuple(rule for rule in self.rules if rule.matches(target))
        if not matched:
            return Resolution(decision=self.default, rule=None, subject=subject)
        best = max(rule.specificity for rule in matched)
        finalists = [rule for rule in matched if rule.specificity == best]
        decision = most_restrictive([r.decision for r in finalists], default=self.default)
        # Last wins among equals: later layers are edited more recently, so pointing at
        # the later file is what makes provenance actionable.
        winner = [rule for rule in finalists if rule.decision is decision][-1]
        return Resolution(decision=decision, rule=winner, matched=matched, subject=subject)


# --------------------------------------------------------------------------- #
# Asking a human
# --------------------------------------------------------------------------- #


class Outcome(StrEnum):
    """The four answers. Not a boolean, because two of them carry consequences."""

    YES_ONCE = "yes_once"
    YES_SESSION = "yes_session"
    YES_PERSIST = "yes_persist"
    NO = "no"

    @property
    def approves(self) -> bool:
        return self is not Outcome.NO

    @property
    def remembers(self) -> bool:
        return self in {Outcome.YES_SESSION, Outcome.YES_PERSIST}


@dataclass(frozen=True, slots=True)
class Answer:
    """A human's reply: an outcome, plus the words that make a ``no`` useful."""

    outcome: Outcome
    feedback: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, Outcome):
            raise TypeError("Answer.outcome must be an Outcome")

    @property
    def approves(self) -> bool:
        return self.outcome.approves


@runtime_checkable
class Asker(Protocol):
    """Whatever puts the question to a human. Injected so tests need no UI."""

    async def ask(self, request: ApprovalRequest) -> Answer:
        """Answer one request. Must return; a hung asker hangs the turn."""
        ...


@dataclass(frozen=True, slots=True)
class UnattendedAsker:
    """The safe default when nobody is watching: always ``no``, and says why.

    Mirrors ``core.types.DENY_UNATTENDED``. A headless run that auto-approved would be
    the single worst default in the system, so the default asker refuses.
    """

    async def ask(self, request: ApprovalRequest) -> Answer:
        return Answer(
            outcome=Outcome.NO,
            feedback=(
                "no human is attached to approve this, so it was refused. Either do "
                "this a different way, or tell the user what to run themselves."
            ),
        )


# --------------------------------------------------------------------------- #
# The builtin allowlist
# --------------------------------------------------------------------------- #

#: Read-only inspection. None of these can change a byte, so gating them buys nothing
#: and costs a prompt — which is the trade that gets a gate turned off.
READ_ONLY_BINARIES: tuple[str, ...] = (
    "ls",
    "pwd",
    "cd",
    "pushd",
    "popd",
    "echo",
    "printf",
    "cat",
    "bat",
    "head",
    "tail",
    "wc",
    "sort",
    "uniq",
    "cut",
    "tr",
    "column",
    "diff",
    "cmp",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "ack",
    "find",
    "fd",
    "file",
    "stat",
    "which",
    "type",
    "date",
    "printenv",
    "env",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "tree",
    "du",
    "df",
    "ps",
    "uname",
    "whoami",
    "hostname",
    "id",
    "sleep",
    "true",
    "false",
    "test",
    "seq",
    "jq",
    "yq",
    "awk",
    "sed",
    "tee",
    "xxd",
    "md5sum",
    "sha256sum",
    "nproc",
    "locale",
    "man",
    "curl",
    "wget",
    "host",
    "dig",
    "ping",
    "export",
    "unset",
    "clear",
    "history",
)

#: Toolchains. A build, a test run or a linter is the work itself; asking about them is
#: asking about the task the user just gave you.
DEV_BINARIES: tuple[str, ...] = (
    "python",
    "python3",
    "uv",
    "uvx",
    "pip",
    "pip3",
    "pipx",
    "poetry",
    "hatch",
    "tox",
    "nox",
    "pytest",
    "ruff",
    "mypy",
    "black",
    "isort",
    "flake8",
    "pylint",
    "coverage",
    "node",
    "npm",
    "npx",
    "pnpm",
    "yarn",
    "bun",
    "deno",
    "tsc",
    "eslint",
    "prettier",
    "jest",
    "vitest",
    "make",
    "cmake",
    "ninja",
    "cargo",
    "rustc",
    "rustup",
    "go",
    "gofmt",
    "golangci-lint",
    "javac",
    "java",
    "mvn",
    "gradle",
    "dotnet",
    "swift",
    "ruby",
    "bundle",
    "rake",
    "gem",
    "php",
    "composer",
    "just",
    "task",
    "terraform",
    "kubectl",
    "helm",
)

#: git subcommands that are read-only or trivially recoverable. ``checkout``, ``clean``,
#: ``rebase``, ``merge``, ``restore`` and ``switch`` are absent on purpose: each can
#: destroy uncommitted work, and each is worth one prompt.
SAFE_GIT_SUBCOMMANDS: tuple[str, ...] = (
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "rev-parse",
    "rev-list",
    "describe",
    "blame",
    "ls-files",
    "ls-tree",
    "remote",
    "tag",
    "worktree",
    "shortlog",
    "cat-file",
    "symbolic-ref",
    "config",
    "stash",
    "fetch",
    "add",
    "commit",
    "pull",
    "bisect",
    "reflog",
    "grep",
    "apply",
    "diff-tree",
    "for-each-ref",
    "check-ignore",
    "init",
)

#: Mutations that are cheap to undo, so a prompt costs more than it saves. Their paths
#: are still checked against the deny list, which is what keeps them cheap *and* safe.
CHEAP_MUTATION_BINARIES: tuple[str, ...] = (
    "mkdir",
    "rmdir",
    "touch",
    "cp",
    "mv",
    "ln",
    "tar",
    "zip",
    "unzip",
    "gzip",
    "gunzip",
    "chmod",
    "truncate",
    "install",
)


def _alternation(names: Sequence[str]) -> str:
    return "|".join(re.escape(name) for name in names)


def builtin_rules() -> tuple[Rule, ...]:
    """The rules that ship. Broad allow, one tool-wide ask, two unwaivable gates.

    Returned from a function rather than held as a module constant so no caller can
    mutate the shipped policy in place — a mutable global allowlist is a security hole
    with a very short fuse.
    """

    def rule(pattern: str, decision: Decision, reason: str, *, unwaivable: bool = False) -> Rule:
        return Rule(
            tool="bash",
            matcher=CommandRegex(pattern),
            decision=decision,
            source=BUILTIN_SOURCE,
            reason=reason,
            unwaivable=unwaivable,
        )

    return (
        # The floor for the shell: anything not recognised is asked about.
        Rule(
            tool="bash",
            matcher=AnyUse(),
            decision=Decision.ASK,
            source=BUILTIN_SOURCE,
            reason="an unrecognised command is confirmed once",
        ),
        rule(rf"^({_alternation(READ_ONLY_BINARIES)})\b", Decision.ALLOW, "read-only"),
        rule(rf"^({_alternation(DEV_BINARIES)})\b", Decision.ALLOW, "build/test toolchain"),
        rule(
            rf"^git ({_alternation(SAFE_GIT_SUBCOMMANDS)})\b",
            Decision.ALLOW,
            "read-only or recoverable git",
        ),
        rule(r"^git$", Decision.ALLOW, "bare `git` prints help"),
        rule(
            rf"^({_alternation(CHEAP_MUTATION_BINARIES)})\b",
            Decision.ALLOW,
            "cheap, recoverable mutation; paths still go through the deny list",
        ),
        # `rm` is allowed only when it is not recursive. The negative lookahead is the
        # whole point: `rm file` is routine, `rm -rf dir` deserves a look.
        rule(
            r"^rm\b(?!.*(?:-[a-zA-Z]*[rR]|--recursive))",
            Decision.ALLOW,
            "non-recursive delete",
        ),
        # Always asked, never remembered: both leave the machine or rewrite history,
        # and "remember this" on either is how a repo loses commits at 2am.
        rule(
            r"^git push\b",
            Decision.ASK,
            "a push leaves this machine and cannot be undone locally",
            unwaivable=True,
        ),
        rule(
            r"^git (reset|rebase|checkout|clean|restore|switch|cherry-pick|merge)\b",
            Decision.ASK,
            "can discard uncommitted work",
            unwaivable=True,
        ),
    )


#: Ruleset used when no configuration has been loaded at all.
def builtin_ruleset() -> RuleSet:
    return RuleSet(rules=builtin_rules(), default=Decision.ASK)


# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Verdict:
    """Everything policy concluded before a human was asked, and why.

    Kept as a value so ``/doctor`` and the tests can inspect a decision without
    re-deriving it, and so the reason text shown to a human is the same text the model
    is given on a refusal.
    """

    decision: Decision
    reason: str
    trace: tuple[str, ...]
    resolutions: tuple[Resolution, ...] = ()
    deny_hits: tuple[DenyHit, ...] = ()
    found_hazards: tuple[Hazard, ...] = ()
    taint: TaintHit | None = None
    waivable: bool = True

    @property
    def sources(self) -> tuple[str, ...]:
        """Which config layers had a say. What ``/doctor`` prints."""
        seen: list[str] = []
        for resolution in self.resolutions:
            if resolution.source not in seen:
                seen.append(resolution.source)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One decision, after any human involvement. The session's permission history."""

    tool: str
    subject: str
    decision: Decision
    approved: bool
    outcome: Outcome | None
    reason: str
    trace: tuple[str, ...]


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #

#: Hazard severity, as a policy floor. The mapping is the one place a structural risk
#: becomes a permission decision, which is why it is a table and not scattered ifs.
HAZARD_FLOOR: Mapping[Severity, Decision] = {
    Severity.NOTE: Decision.ALLOW,
    Severity.ASK: Decision.ASK,
    Severity.BLOCK: Decision.DENY,
}


@dataclass(slots=True)
class PolicyEngine:
    """Satisfies ``ronin.core.protocols.Policy``.

    Mutable — deliberately, and this is the only mutable thing in the package: session
    rules accumulate as a human says "yes, and remember for this session", and
    cancellation is a flag the UI flips from outside the turn. Both are session state
    with exactly one owner, which is the shape the conventions ask for; everything else
    here is a frozen value.
    """

    rules: RuleSet
    asker: Asker = field(default_factory=UnattendedAsker)
    denylist: Denylist | None = None
    mode: Mode = Mode.ASK
    taint: TaintTracker | None = None
    sandbox: Sandbox | None = None
    persist: Callable[[Rule], None] | None = None
    _session_rules: list[Rule] = field(default_factory=list, repr=False)
    _audit: list[AuditEntry] = field(default_factory=list, repr=False)
    _cancelled: bool = False

    # -- Policy protocol ---------------------------------------------------- #

    async def approve(self, spec: ToolSpec, use: ToolUse, *, rendered: str) -> ApprovalDecision:
        """Decide one gated call, asking a human only when policy says ``ask``."""
        if self._cancelled:
            return ApprovalDecision(
                approved=False,
                reason="the user cancelled this turn; stop and wait for their next message",
            )
        verdict = self.evaluate(spec, use)
        shown = rendered or self.render(spec, use)

        if verdict.decision is Decision.DENY:
            self._record(spec, use, verdict, approved=False, outcome=None, reason=verdict.reason)
            return ApprovalDecision(approved=False, reason=verdict.reason)

        if self.sandbox is not None and self.sandbox.isolates:
            # Documented policy decision: inside an isolating sandbox the blast radius
            # is the sandbox, so there is nothing left for a prompt to protect. The
            # deny list still bites above, because unconditional means unconditional.
            reason = SANDBOX_AUTO_APPROVES
            self._record(spec, use, verdict, approved=True, outcome=None, reason=reason)
            return ApprovalDecision(approved=True, reason=reason)

        if verdict.decision is Decision.ALLOW:
            self._record(spec, use, verdict, approved=True, outcome=None, reason=verdict.reason)
            return ApprovalDecision(approved=True, reason=verdict.reason)

        answer = await self.asker.ask(
            ApprovalRequest(
                tool_use_id=use.id,
                name=spec.name,
                danger_level=spec.danger_level,
                rendered=shown,
                reason=verdict.reason,
            )
        )
        return self._apply(spec, use, verdict, answer)

    def check_budget(self, budget: Budget) -> str | None:
        """Which ceiling was hit, or ``None``. Names the limit so the UI can say which."""
        if budget.max_tokens is not None and budget.spent_tokens >= budget.max_tokens:
            return "token_budget"
        if budget.max_usd is not None and budget.spent_usd >= budget.max_usd:
            return "cost_budget"
        wall = budget.max_wall_seconds
        if wall is not None and budget.elapsed_seconds >= wall:
            return "wall_budget"
        return None

    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """Ask the loop to stop. Idempotent, and never un-set from inside a turn."""
        self._cancelled = True

    # -- inspection --------------------------------------------------------- #

    @property
    def session_rules(self) -> tuple[Rule, ...]:
        """Rules added by "yes, and remember for this session"."""
        return tuple(self._session_rules)

    @property
    def audit(self) -> tuple[AuditEntry, ...]:
        """Every decision this engine made, oldest first."""
        return tuple(self._audit)

    def effective_rules(self) -> RuleSet:
        return self.rules.with_rules(self._session_rules)

    def render(self, spec: ToolSpec, use: ToolUse) -> str:
        """A fallback rendering, so an approval request is never blank."""
        command = use.arguments.get(COMMAND_ARGUMENT)
        if isinstance(command, str) and command.strip():
            return command
        parts = ", ".join(f"{key}={value!r}" for key, value in sorted(use.arguments.items()))
        return f"{spec.name}({parts})" if parts else spec.name

    # -- evaluation --------------------------------------------------------- #

    def evaluate(self, spec: ToolSpec, use: ToolUse) -> Verdict:
        """Policy's answer with no human involved. Pure with respect to engine state."""
        rules = self.effective_rules()
        segments = self._segments(use)
        trace: list[str] = []

        resolutions = self._resolutions(rules, spec, use, segments)
        rule_decision = most_restrictive(
            [resolution.decision for resolution in resolutions], default=rules.default
        )
        for resolution in resolutions:
            trace.append(f"rules: {resolution.explain()}")

        found = hazards(segments)
        # An `ask` hazard is a request for one confirmation, and a human who approved
        # this *exact* command with the hazard in front of them has given it. Without
        # this, "yes, remember for this session" would be useless for everything that
        # actually gets gated — the second `sed -i` would prompt again, and a gate that
        # cannot learn is a gate people switch off. A `block` hazard is not a request
        # and is never lifted; neither is the deny list or a taint escalation.
        settled = self._approved_exactly(resolutions)
        weighed = [h for h in found if h.severity is Severity.BLOCK or not settled]
        hazard_decision = most_restrictive(
            [HAZARD_FLOOR[h.severity] for h in weighed], default=Decision.ALLOW
        )
        trace.extend(f"hazard: {hazard}" for hazard in found)
        if settled and len(weighed) != len(found):
            trace.append("an exact rule approved this command, so its `ask` hazards are met")

        deny_hits = self._deny_hits(spec, use, segments)
        trace.extend(f"denylist: {hit.code.value} on `{hit.subject}`" for hit in deny_hits)

        taint_hit = self.taint.derives_from_untrusted(use.arguments) if self.taint else None
        if taint_hit is not None:
            trace.append(f"taint: {taint_hit.describe()}")

        relaxed = self._relax(rule_decision, spec)
        if relaxed is not rule_decision:
            trace.append(f"mode {self.mode.value}: relaxed {rule_decision.value} to allow")
        floors = [relaxed, hazard_decision]
        if deny_hits:
            floors.append(Decision.DENY)
        if taint_hit is not None:
            floors.append(Decision.ASK)
        if self.mode is Mode.PLAN and spec.danger_level >= DangerLevel.MUTATING:
            floors.append(Decision.DENY)
            trace.append("mode plan: nothing may mutate")

        decision = most_restrictive(floors, default=Decision.ASK)
        waivable = self._waivable(decision, spec, resolutions, taint_hit)
        return Verdict(
            decision=decision,
            reason=self._reason(
                decision, spec, resolutions, deny_hits, found, taint_hit, rule_decision
            ),
            trace=tuple(trace),
            resolutions=tuple(resolutions),
            deny_hits=deny_hits,
            found_hazards=found,
            taint=taint_hit,
            waivable=waivable,
        )

    def _segments(self, use: ToolUse) -> tuple[Segment, ...]:
        command = use.arguments.get(COMMAND_ARGUMENT)
        if not isinstance(command, str) or not command.strip():
            return ()
        return parse_command(command)

    def _resolutions(
        self,
        rules: RuleSet,
        spec: ToolSpec,
        use: ToolUse,
        segments: Sequence[Segment],
    ) -> tuple[Resolution, ...]:
        """One resolution per segment, and the whole call only for an exact match.

        Two failures shaped this, and both are worth stating because the obvious
        implementations hit them:

        *A regex may not be matched against the whole raw command.* ``^echo\\b`` matches
        the string ``echo safe; rm -rf /``, so an allowlist evaluated against the raw
        text would approve the very command this package exists to stop. Only per-segment
        matching is trustworthy for patterns.

        *But an* :class:`Exact` *rule on the whole command line must still work*, or
        "yes, remember this command" could never cover a compound command — the
        remembered rule would match the whole use while an unmatched segment dragged the
        answer back to ``ask``. Exact is safe here precisely because it is exact: it
        approves one byte-for-byte string and generalises to nothing.
        """
        whole = rules.resolve(MatchTarget(tool=spec.name, arguments=use.arguments))
        if not segments:
            return (whole,)
        per_segment = [
            rules.resolve(MatchTarget(tool=spec.name, arguments=use.arguments, segment=segment))
            for segment in segments
            if self._segment_counts(segment)
        ]
        exact = whole.rule is not None and whole.rule.matcher.specificity >= Exact.specificity
        if exact:
            # Unmatched segments stop contributing their default, but any segment with a
            # rule of its own still speaks, and most-restrictive still applies.
            explicit = [
                resolution
                for resolution in per_segment
                if resolution.rule is not None and resolution.rule.matcher.specificity > 0
            ]
            return (whole, *explicit)
        return tuple(per_segment) or (whole,)

    def _approved_exactly(self, resolutions: Sequence[Resolution]) -> bool:
        """Whether an :class:`Exact` rule allowed this precise call.

        Exact is the only matcher that can carry this weight: it approves one
        byte-for-byte string and generalises to nothing, so "the human already said yes
        to this" is a claim about the same command and not about a family of them.
        """
        return any(
            resolution.decision is Decision.ALLOW
            and resolution.rule is not None
            and resolution.rule.matcher.specificity >= Exact.specificity
            for resolution in resolutions
        )

    def _segment_counts(self, segment: Segment) -> bool:
        """Whether a segment runs anything. ``FOO=bar`` on its own does not."""
        return bool(segment.binary) or any(r.names_a_file for r in segment.redirects)

    def _deny_hits(
        self, spec: ToolSpec, use: ToolUse, segments: Sequence[Segment]
    ) -> tuple[DenyHit, ...]:
        if self.denylist is None:
            return ()
        hits: list[DenyHit] = list(self.denylist.check_segments(segments))
        command = use.arguments.get(COMMAND_ARGUMENT)
        if isinstance(command, str):
            hits.extend(self.denylist.check_command_text(command))
        writes = spec.danger_level >= DangerLevel.MUTATING
        for key, value in sorted(use.arguments.items()):
            if key not in PATH_ARGUMENTS:
                continue
            for path in _as_paths(value):
                hits.extend(self.denylist.check_path(path, write=writes))
        return tuple(hits)

    def _relax(self, decision: Decision, spec: ToolSpec) -> Decision:
        """A permissive mode may turn ``ask`` into ``allow``, and nothing else."""
        if decision is not Decision.ASK:
            return decision
        if not spec.requires_approval and spec.danger_level is DangerLevel.READ_ONLY:
            # A tool that cannot change anything and declares it needs no approval is
            # not worth a prompt in any mode. ``ToolSpec`` already refuses to let a
            # destructive tool make that declaration, so this cannot be abused by a
            # spec author. Without it, `read` with no rule configured would prompt, and
            # a gate that prompts on a file read is a gate that gets switched off.
            return Decision.ALLOW
        if self.mode is Mode.FULL:
            return Decision.ALLOW
        if self.mode is Mode.AUTO_EDIT and not spec.requires_approval:
            return Decision.ALLOW
        if self.mode is Mode.AUTO_EDIT and spec.danger_level <= DangerLevel.MUTATING:
            return Decision.ALLOW
        return decision

    def _waivable(
        self,
        decision: Decision,
        spec: ToolSpec,
        resolutions: Sequence[Resolution],
        taint_hit: TaintHit | None,
    ) -> bool:
        """Whether a "remember this" may be honoured for this call.

        Four things cannot be remembered, and each has a reason worth more than the
        convenience: a rule marked ``unwaivable``, an escalation caused by untrusted
        content (remembering that is precisely the hole taint tracking exists to
        close), an irreversible tool, and a denial.
        """
        if decision is Decision.DENY:
            return False
        if taint_hit is not None:
            return False
        if spec.danger_level >= DangerLevel.IRREVERSIBLE:
            return False
        return not any(
            resolution.rule is not None and resolution.rule.unwaivable for resolution in resolutions
        )

    def _reason(
        self,
        decision: Decision,
        spec: ToolSpec,
        resolutions: Sequence[Resolution],
        deny_hits: Sequence[DenyHit],
        found: Sequence[Hazard],
        taint_hit: TaintHit | None,
        rule_decision: Decision,
    ) -> str:
        """The sentence a human reads in the prompt and a model reads on refusal."""
        if deny_hits:
            head = deny_hits[0].message
            if len(deny_hits) > 1:
                others = ", ".join(hit.code.value for hit in deny_hits[1:])
                head = f"{head}\n(also refused for: {others})"
            return head
        blocking = [hazard for hazard in found if hazard.severity is Severity.BLOCK]
        if decision is Decision.DENY and blocking:
            return (
                f"refused: {blocking[0].note}.\nThe command was `{blocking[0].segment}`. "
                "Rewrite it so each step is visible, then try again."
            )
        if decision is Decision.DENY and self.mode is Mode.PLAN:
            return (
                "plan mode is read-only, so this was not run. Describe the change you "
                "would make and let the user switch modes if they want it applied."
            )
        if decision is Decision.DENY:
            denied = [r for r in resolutions if r.decision is Decision.DENY]
            detail = denied[0].explain() if denied else "policy denied it"
            return (
                f"refused by policy: {detail}. If this is wrong, the user can change "
                "the rule in .ronin/settings.json."
            )
        parts: list[str] = []
        if taint_hit is not None:
            parts.append(
                "these arguments quote content fetched from "
                f"{taint_hit.source!r}, so this needs a human even though the command "
                "itself is allowed"
            )
        asked = [hazard for hazard in found if hazard.severity is Severity.ASK]
        parts.extend(hazard.note for hazard in asked)
        if rule_decision is Decision.ASK and not parts:
            gating = [r for r in resolutions if r.decision is Decision.ASK]
            if gating and gating[0].rule is not None:
                parts.append(
                    f"not on the allowlist, so it needs one confirmation "
                    f"({gating[0].rule.describe()})"
                )
            elif gating:
                parts.append(
                    "no rule covers this, and the ruleset default is to ask. Approving "
                    "for the session or writing a rule will stop it asking again."
                )
        return "; ".join(parts)

    # -- applying an answer -------------------------------------------------- #

    def _apply(
        self, spec: ToolSpec, use: ToolUse, verdict: Verdict, answer: Answer
    ) -> ApprovalDecision:
        if not answer.approves:
            reason = self._denial(answer.feedback)
            self._record(spec, use, verdict, approved=False, outcome=answer.outcome, reason=reason)
            return ApprovalDecision(approved=False, reason=reason)

        note = answer.feedback
        remember = False
        if answer.outcome.remembers and not verdict.waivable:
            note = (
                (f"{note} " if note else "")
                + "approved once. This action is always confirmed, so the approval was "
                "not remembered — you will be asked again next time."
            ).strip()
        elif answer.outcome.remembers:
            rule = self.remembered_rule(spec, use, answer.outcome)
            self._session_rules.append(rule)
            if answer.outcome is Outcome.YES_PERSIST:
                remember = True
                if self.persist is not None:
                    self.persist(rule)
                note = (f"{note} " if note else "") + "approved and written as a standing rule."
            else:
                note = (f"{note} " if note else "") + "approved for the rest of this session."
            note = note.strip()
        self._record(spec, use, verdict, approved=True, outcome=answer.outcome, reason=note)
        return ApprovalDecision(approved=True, reason=note, remember=remember)

    def remembered_rule(self, spec: ToolSpec, use: ToolUse, outcome: Outcome) -> Rule:
        """The rule a "yes, remember" turns into.

        An :class:`Exact` match on what was actually approved, never a pattern: a human
        who approves ``rm -rf build`` has not approved ``rm -rf`` anything, and a
        generalising "remember" is how an allowlist grows teeth overnight.
        """
        source = "session" if outcome is Outcome.YES_SESSION else "remembered"
        command = use.arguments.get(COMMAND_ARGUMENT)
        if isinstance(command, str) and command.strip():
            matcher: Matcher = Exact(argument=COMMAND_ARGUMENT, value=command)
        else:
            path = next(
                (
                    value
                    for key in sorted(PATH_ARGUMENTS)
                    for value in _as_paths(use.arguments.get(key))
                ),
                None,
            )
            matcher = Exact(argument="path", value=path) if path is not None else AnyUse()
        return Rule(
            tool=spec.name,
            matcher=matcher,
            decision=Decision.ALLOW,
            source=source,
            reason="approved by the user",
        )

    def _denial(self, feedback: str) -> str:
        """Turn a human's "no" into something the model can act on.

        The feedback is reproduced verbatim and first. This is the whole reason
        "no with feedback" is one of the four outcomes: a refusal the model can read as
        a redirection ("use the staging database") keeps the turn alive, and a bare
        "denied" makes it retry the same call with different quoting.
        """
        if feedback.strip():
            return (
                f"the user declined and said: {feedback.strip()}\n"
                "Take that as a correction, not a dead end: adjust the plan and continue."
            )
        return (
            "the user declined this action and gave no reason. Do not retry it. "
            "Propose an alternative, or ask them what they would prefer."
        )

    def _record(
        self,
        spec: ToolSpec,
        use: ToolUse,
        verdict: Verdict,
        *,
        approved: bool,
        outcome: Outcome | None,
        reason: str,
    ) -> None:
        self._audit.append(
            AuditEntry(
                tool=spec.name,
                subject=self.render(spec, use),
                decision=verdict.decision,
                approved=approved,
                outcome=outcome,
                reason=reason,
                trace=verdict.trace,
            )
        )


def _as_paths(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


__all__ = [
    "BUILTIN_SOURCE",
    "CHEAP_MUTATION_BINARIES",
    "COMMAND_ARGUMENT",
    "DEV_BINARIES",
    "HAZARD_FLOOR",
    "MATCHER_KINDS",
    "PATH_ARGUMENTS",
    "READ_ONLY_BINARIES",
    "SAFE_GIT_SUBCOMMANDS",
    "Answer",
    "AnyUse",
    "Asker",
    "AuditEntry",
    "CommandRegex",
    "Decision",
    "Exact",
    "MatchTarget",
    "Matcher",
    "Outcome",
    "PathGlob",
    "PolicyEngine",
    "Resolution",
    "Rule",
    "RuleSet",
    "UnattendedAsker",
    "Verdict",
    "builtin_rules",
    "builtin_ruleset",
    "glob_to_regex",
    "most_restrictive",
]
