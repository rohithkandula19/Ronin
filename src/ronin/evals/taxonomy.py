"""The failure taxonomy: six classes, each derived from evidence, never guessed.

The taxonomy is the reason the suite exists. A pass rate says the agent is worse
than you want; the taxonomy says **whether to fix the harness or the model**.
``EDIT_AMBIGUITY`` and ``HALLUCINATED_API`` are harness problems — the model knew
what it wanted and the tool surface would not let it say so. ``WRONG_FILE``,
``GAVE_UP_EARLY``, ``LOOPED`` and ``BROKE_TESTS`` are model problems. A bucket that
cannot tell those apart is a bucket nobody acts on.

Four rules hold this module together.

**Every classifier is a pure function over recorded evidence.** No classifier reads
a file, runs a command, or consults a model. :class:`RunRecord` is the whole input,
which is what makes each class testable from a literal and what makes the runner's
job explicit: if the runner does not record it, the taxonomy cannot see it.

**The classifier set is ordered and total.** :data:`CLASSIFIERS` is a tuple, and its
order *is* the ranking. A failure matching nothing is :attr:`FailureClass.UNCLASSIFIED`
and is counted prominently, because the real risk here is a taxonomy that quietly
absorbs everything into whichever bucket happens to match first and then reads as
insight.

**Several classes may apply, and all of them are returned.** An agent that edits the
wrong file, breaks a test doing it, and then loops is one run with three findings.
Returning only the first would hide the two that mattered. The ranking is by strength
of evidence, not by severity: a fired stall detector and a tool name absent from the
registry are machine-certain; "the final message hedges" is a heuristic and is
therefore last.

**A skip is not a failure and a pass is not classified.** Both return no findings, so
the taxonomy denominator is failures and nothing else.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final

from ..core.loop import STALL_REPEATS

# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #

#: Tools whose failure means "the model could not express the edit it wanted".
#: Named rather than inferred from danger level, because ``bash`` is also mutating
#: and a failing shell command is not an edit-expression problem.
EDIT_TOOLS: Final[frozenset[str]] = frozenset({"edit", "multi_edit", "write"})

#: Substrings that identify an edit failure as *ambiguity or staleness*, paired with
#: the diagnosis they support. These are the literal messages
#: ``ronin.tools.files.apply_edit`` and ``EditTool`` produce; matching on the message
#: rather than on an error code is a coupling, and it is the honest one — the tool
#: layer returns errors as prose for the model, and there is no code to match.
EDIT_AMBIGUITY_MARKERS: Final[tuple[tuple[str, str], ...]] = (
    ("so replacing it would be ambiguous", "old_string matched more than one place"),
    ("old_string appears", "old_string matched more than one place"),
    ("was not found in", "old_string did not match the file's current contents"),
    ("has not been read in this session", "edit attempted against unread content"),
)

#: Exception names that, together with a symbol the fixture does not define, mean the
#: model invented an API.
HALLUCINATION_EXCEPTIONS: Final[tuple[str, ...]] = (
    "AttributeError",
    "ImportError",
    "ModuleNotFoundError",
    "NameError",
)

_SYMBOL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"has no attribute '([^']+)'"),
    re.compile(r"cannot import name '([^']+)'"),
    re.compile(r"name '([^']+)' is not defined"),
    re.compile(r"No module named '([^']+)'"),
)

#: Phrases that mark a final message as a description of work instead of work. Kept
#: short and specific: a long list of "sounds unsure" phrases would fire on the
#: normal English of an agent that *did* the job, and this classifier is already
#: gated on the run having produced no edits at all.
HEDGE_MARKERS: Final[tuple[str, ...]] = (
    "you could",
    "you should",
    "you would need",
    "you'll need to",
    "i would suggest",
    "i recommend",
    "let me know if",
    "i am unable to",
    "i'm unable to",
    "i cannot",
    "i can't",
    "would need to be",
    "should be modified",
)

#: How a failing check is recognised in captured output, per test runner. Used only
#: to compare *before* against *after*: a check that fails after and did not fail
#: before is a regression the agent introduced, which is what BROKE_TESTS means.
_FAILING_CHECK_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE),  # pytest
    re.compile(r"^not ok \d+\s*-?\s*(.+)$", re.MULTILINE),  # TAP
    re.compile(r"^--- FAIL: (\S+)", re.MULTILINE),  # go test
    # unittest, both with and without the "(module.Class)" it prints in verbose mode.
    re.compile(r"^(\S+)(?: \(\S+\))? \.\.\. (?:FAIL|ERROR)\s*$", re.MULTILINE),
)


def failing_checks(output: str) -> frozenset[str]:
    """Identifiers of individual checks that failed, as far as output reveals them.

    Deliberately a *set of names*, not a count: comparing counts across two runs says
    "more things fail now", comparing names says *which* thing the agent broke, and
    only the second is actionable. Output no pattern matches yields an empty set,
    which makes :func:`classify_broke_tests` fall back to the exit-code signal.
    """
    found: set[str] = set()
    for pattern in _FAILING_CHECK_PATTERNS:
        found.update(match.group(1).strip() for match in pattern.finditer(output))
    return frozenset(name for name in found if name)


def missing_symbols(text: str) -> tuple[str, ...]:
    """Symbols a Python error says do not exist, in order of appearance.

    Requires one of :data:`HALLUCINATION_EXCEPTIONS` to be named in the text, so a
    line that merely quotes the words "has no attribute" is not evidence.
    """
    if not any(name in text for name in HALLUCINATION_EXCEPTIONS):
        return ()
    found: list[str] = []
    for pattern in _SYMBOL_PATTERNS:
        for match in pattern.finditer(text):
            # A dotted module name fails on its first component; that is the name a
            # symbol table can be checked against.
            found.append(match.group(1).split(".")[0])
    return tuple(dict.fromkeys(found))


def fixture_symbols(root: str | Path) -> frozenset[str]:
    """Every name a Python fixture defines *or mentions*, plus its module names.

    Deliberately over-inclusive — bindings, attribute names, parameters and import
    aliases all count. This table is used only to *disprove* a hallucination, so a
    name that is merely referenced still makes the check conservative: the cost of a
    missing name is a false accusation, and the cost of an extra one is a failure
    that falls through to ``UNCLASSIFIED``, which is loud.

    Parsed with ``ast`` rather than imported: importing a fixture would execute it,
    and a fixture that is broken on purpose (most of them) would take the runner
    down with it. A file that does not parse contributes its module name and nothing
    else, which is the honest answer — a syntax error means we do not know what it
    defines.
    """
    base = Path(root)
    if not base.is_dir():
        return frozenset()
    names: set[str] = set()
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        names.add(path.stem)
        names.update(part for part in path.relative_to(base).parts[:-1])
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                names.add((node.asname or node.name).split(".")[0])
    return frozenset(names)


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One tool call the agent made, and how it ended.

    ``fingerprint`` is ``ronin.core.loop.fingerprint`` of the call, so "the same
    action" here means exactly what the loop's own stall detector means by it —
    two definitions of sameness would put the taxonomy and the loop in disagreement
    about whether a run looped.
    """

    name: str
    fingerprint: str = ""
    ok: bool = True
    error: str = ""
    content: str = ""
    paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Tokens and cost for one task run — with "not reported" as a first-class state.

    Follows ``ronin.providers.accounting``: **unreported is not zero.** A provider
    that sends no usage produces ``reported=False`` and zero counters, and every
    aggregate must carry the unreported count alongside the totals rather than
    averaging a zero in. The same distinction applies to ``priced``: an unpriced
    model's cost is a floor, not a total.
    """

    reported: bool = False
    priced: bool = False
    #: A total, for the provider seams that only give one (``Budget.spent_tokens``).
    #: Zero here with a non-zero split is normal; both zero with ``reported=True``
    #: means the provider genuinely used none.
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    requests: int = 0
    unreported_requests: int = 0

    @property
    def total_tokens(self) -> int:
        return self.tokens or (self.input_tokens + self.output_tokens)

    def render_tokens(self) -> str:
        return f"{self.total_tokens:,}" if self.reported else "unreported"

    def render_cost(self) -> str:
        if not self.reported:
            return "unreported"
        return f"${self.cost_usd:.4f}" if self.priced else f"${self.cost_usd:.4f} (floor, unpriced)"


@dataclass(frozen=True, slots=True)
class VerifyProbe:
    """One run of a task's ``verify.sh``, or the reason there was not one."""

    ran: bool = False
    exit_code: int = -1
    output: str = ""
    duration_s: float = 0.0
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.ran and self.exit_code == 0

    @property
    def failing(self) -> frozenset[str]:
        return failing_checks(self.output)


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    """What one adapter observed the agent do. The adapter's whole return value.

    Deliberately narrower than :class:`RunRecord`: an adapter knows what the agent
    did, and nothing about baselines, verification or wall-clock accounting. Keeping
    those out of the protocol is what lets a second adapter (the v1 shell-out) be
    thirty lines instead of a reimplementation of the runner.
    """

    turns_used: int = 0
    tool_calls: tuple[ToolCallRecord, ...] = ()
    edited_paths: tuple[str, ...] = ()
    final_text: str = ""
    stalled: bool = False
    stop_reason: str = ""
    registered_tools: tuple[str, ...] = ()
    usage: UsageRecord = field(default_factory=UsageRecord)
    error: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Everything observed about one task run. The taxonomy's only input.

    ``known_symbols`` is the union of the names the *bare fixture* defines and the
    names the *finished workspace* defines, so an API the agent legitimately added
    is not mistaken for one it invented, while a call to something that exists in
    neither still is.
    """

    task_id: str
    category: str = ""
    prompt: str = ""
    files_expected: tuple[str, ...] = ()
    outcome: AgentOutcome = field(default_factory=AgentOutcome)
    verify_before: VerifyProbe = field(default_factory=VerifyProbe)
    verify_after: VerifyProbe = field(default_factory=VerifyProbe)
    known_symbols: frozenset[str] = frozenset()
    wall_seconds: float = 0.0
    timed_out: bool = False
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""
    workspace: str = ""

    # Convenience views, so a classifier reads as prose instead of as `.outcome.`.
    @property
    def tool_calls(self) -> tuple[ToolCallRecord, ...]:
        return self.outcome.tool_calls

    @property
    def edited_paths(self) -> tuple[str, ...]:
        return self.outcome.edited_paths

    @property
    def turns_used(self) -> int:
        return self.outcome.turns_used

    @property
    def final_text(self) -> str:
        return self.outcome.final_text

    @property
    def usage(self) -> UsageRecord:
        return self.outcome.usage

    @property
    def failed_run(self) -> str:
        """The runner- or adapter-level failure, if any. Empty when the agent ran."""
        return self.error or self.outcome.error

    @property
    def passed(self) -> bool:
        """A pass is a green ``verify.sh`` and nothing else.

        Not "the expected files were touched", and not "the agent said it was done".
        A task with no ``verify.sh`` is therefore never a pass — it is
        :attr:`unverifiable`, counted separately, because inventing a pass criterion
        for it would put a number in the report that no check produced.
        """
        return (
            not self.skipped
            and not self.timed_out
            and not self.failed_run
            and self.verify_after.passed
        )

    @property
    def unverifiable(self) -> bool:
        return not self.skipped and not self.verify_after.ran

    @property
    def failed(self) -> bool:
        return not self.skipped and not self.passed


# --------------------------------------------------------------------------- #
# The classes
# --------------------------------------------------------------------------- #


class FailureClass(StrEnum):
    """The six classes, plus the honest seventh state.

    ``UNCLASSIFIED`` is not a class of failure; it is an admission that the evidence
    recorded does not explain this one. Its count is the taxonomy's own error bar.
    """

    LOOPED = "looped"
    HALLUCINATED_API = "hallucinated_api"
    EDIT_AMBIGUITY = "edit_ambiguity"
    BROKE_TESTS = "broke_tests"
    WRONG_FILE = "wrong_file"
    GAVE_UP_EARLY = "gave_up_early"
    UNCLASSIFIED = "unclassified"


#: Which side of the system each class points at. This mapping is the taxonomy's
#: whole purpose, so it is data rather than a paragraph in a docstring.
BLAME: Final[dict[FailureClass, str]] = {
    FailureClass.LOOPED: "model",
    FailureClass.HALLUCINATED_API: "harness",
    FailureClass.EDIT_AMBIGUITY: "harness",
    FailureClass.BROKE_TESTS: "model",
    FailureClass.WRONG_FILE: "model",
    FailureClass.GAVE_UP_EARLY: "model",
    FailureClass.UNCLASSIFIED: "unknown",
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One class, the observable that triggered it, and its rank.

    ``evidence`` is quoted from the run rather than described, because the first
    question anyone asks a taxonomy is "why do you think that", and a summary cannot
    answer it.
    """

    failure_class: FailureClass
    evidence: str
    rank: int = 0

    @property
    def blame(self) -> str:
        return BLAME[self.failure_class]


def _norm(path: str) -> str:
    """Workspace-relative posix form, so two spellings of one path compare equal."""
    cleaned = path.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return PurePosixPath(cleaned).as_posix()


def _norm_all(paths: Iterable[str]) -> frozenset[str]:
    return frozenset(_norm(path) for path in paths if path.strip())


# --------------------------------------------------------------------------- #
# The classifiers — one small pure function each
# --------------------------------------------------------------------------- #


def classify_looped(record: RunRecord) -> Finding | None:
    """The loop's stall detector fired, or one fingerprint repeated ≥ STALL_REPEATS.

    Both signals are used because they catch different runs: the detector fires only
    inside a single ``run_turn`` call and only after a nudge, while an agent that
    repeats one call across turn boundaries never trips it and still looped.
    """
    if record.outcome.stalled:
        return Finding(
            FailureClass.LOOPED,
            "core.loop's stall detector fired (stop_reason="
            f"{record.outcome.stop_reason or 'stalled'!r})",
        )
    counts: dict[str, int] = {}
    for call in record.tool_calls:
        mark = call.fingerprint or call.name
        counts[mark] = counts.get(mark, 0) + 1
    worst = max(counts.items(), key=lambda item: (item[1], item[0]), default=None)
    if worst is not None and worst[1] >= STALL_REPEATS:
        return Finding(
            FailureClass.LOOPED,
            f"the same call repeated {worst[1]} times (>= {STALL_REPEATS}): {worst[0]}",
        )
    return None


def classify_hallucinated_api(record: RunRecord) -> Finding | None:
    """A call to a tool that is not registered, or an error naming a symbol nowhere.

    The symbol branch is skipped when ``known_symbols`` is empty: with no symbol
    table there is nothing to check against, and asserting a hallucination from a
    bare ``NameError`` would be the guess this module exists to avoid. Such a run
    falls through to ``UNCLASSIFIED``, which is loud, rather than into this bucket,
    which would be quiet and wrong.
    """
    registered = frozenset(record.outcome.registered_tools)
    if registered:
        unknown = [call.name for call in record.tool_calls if call.name not in registered]
        if unknown:
            return Finding(
                FailureClass.HALLUCINATED_API,
                f"called tool(s) not in the registry: {', '.join(sorted(set(unknown)))} "
                f"(registered: {', '.join(sorted(registered))})",
            )
    if not record.known_symbols:
        return None
    for call in record.tool_calls:
        text = f"{call.error}\n{call.content}"
        for symbol in missing_symbols(text):
            if symbol not in record.known_symbols:
                return Finding(
                    FailureClass.HALLUCINATED_API,
                    f"{call.name} reported a missing symbol {symbol!r}, which the "
                    "fixture does not define",
                )
    return None


def classify_edit_ambiguity(record: RunRecord) -> Finding | None:
    """An edit tool refused because the model could not name the text uniquely."""
    for call in record.tool_calls:
        if call.ok or call.name not in EDIT_TOOLS:
            continue
        lowered = call.error.lower()
        for marker, diagnosis in EDIT_AMBIGUITY_MARKERS:
            if marker in lowered:
                return Finding(
                    FailureClass.EDIT_AMBIGUITY,
                    f"{call.name} failed: {diagnosis} — {call.error.strip()[:200]}",
                )
    return None


def classify_broke_tests(record: RunRecord) -> Finding | None:
    """``verify.sh`` fails now, and something that passed on the bare fixture does not.

    Two signals, in order of strength:

    1. The bare fixture verified green and the finished workspace does not. That is
       unambiguous — the only thing that changed is the agent.
    2. Both fail (the normal case: a task exists because its fixture is broken), but
       a *named* check fails after that did not fail before. The names come from
       :func:`failing_checks`; when neither output yields any names this signal is
       unavailable and no finding is produced, which is why signal 1 exists.
    """
    if not record.verify_before.ran or not record.verify_after.ran:
        return None
    if record.verify_after.passed:
        return None
    if record.verify_before.passed:
        return Finding(
            FailureClass.BROKE_TESTS,
            "verify.sh passed on the bare fixture and fails after the run "
            f"(exit {record.verify_after.exit_code})",
        )
    regressed = sorted(record.verify_after.failing - record.verify_before.failing)
    if regressed:
        return Finding(
            FailureClass.BROKE_TESTS,
            f"check(s) failing after the run that passed before: {', '.join(regressed)}",
        )
    return None


def classify_wrong_file(record: RunRecord) -> Finding | None:
    """Every path the agent edited is outside ``files_expected``.

    Requires both sides to be non-empty: with no expectation there is no wrong file,
    and with no edits the failure is :func:`classify_gave_up_early`, not this.
    """
    expected = _norm_all(record.files_expected)
    edited = _norm_all(record.edited_paths)
    if not expected or not edited:
        return None
    if edited & expected:
        return None
    return Finding(
        FailureClass.WRONG_FILE,
        f"edited {', '.join(sorted(edited))} — expected any of {', '.join(sorted(expected))}",
    )


def classify_gave_up_early(record: RunRecord) -> Finding | None:
    """The run ended without acting while ``verify.sh`` still fails.

    Two shapes: nothing was called at all, or something was called but nothing was
    edited and the closing message describes what someone *could* do. The second is
    gated on there being no edits precisely so an agent that did the work and then
    summarised it is not accused of giving up.
    """
    if record.verify_after.passed:
        return None
    if record.edited_paths:
        return None
    if not record.tool_calls:
        return Finding(
            FailureClass.GAVE_UP_EARLY,
            "the run made no tool calls and no edits while verify.sh still fails",
        )
    lowered = record.final_text.lower()
    for marker in HEDGE_MARKERS:
        if marker in lowered:
            return Finding(
                FailureClass.GAVE_UP_EARLY,
                f"no edits were made and the final message hedges ({marker!r})",
            )
    return None


#: The ordered, total classifier set. Order is the ranking: machine-certain evidence
#: first (a fired detector, a tool name absent from the registry), heuristics last
#: (a phrase in the final message).
Classifier = Callable[[RunRecord], "Finding | None"]

CLASSIFIERS: Final[tuple[Classifier, ...]] = (
    classify_looped,
    classify_hallucinated_api,
    classify_edit_ambiguity,
    classify_broke_tests,
    classify_wrong_file,
    classify_gave_up_early,
)


def _unclassified_evidence(record: RunRecord) -> str:
    """Say what *is* known, so an unclassified failure is a lead and not a shrug."""
    parts: list[str] = []
    if record.timed_out:
        parts.append(f"timed out after {record.wall_seconds:.1f}s")
    if record.failed_run:
        parts.append(f"run error: {record.failed_run}")
    if not record.verify_after.ran:
        parts.append(f"verify.sh did not run ({record.verify_after.detail or 'no script'})")
    elif not record.verify_after.passed:
        parts.append(f"verify.sh exit {record.verify_after.exit_code}")
    parts.append(f"{len(record.tool_calls)} tool call(s), {len(record.edited_paths)} edit(s)")
    parts.append(f"{record.turns_used} turn(s)")
    return "; ".join(parts)


def classify(record: RunRecord) -> tuple[Finding, ...]:
    """Every class that applies, ranked. Never empty for a failure.

    A pass and a skip both yield ``()``: the taxonomy's denominator is failures.
    """
    if record.skipped or record.passed:
        return ()
    findings: list[Finding] = []
    for rank, classifier in enumerate(CLASSIFIERS):
        found = classifier(record)
        if found is not None:
            findings.append(Finding(found.failure_class, found.evidence, rank=rank))
    if findings:
        return tuple(findings)
    return (
        Finding(
            FailureClass.UNCLASSIFIED,
            _unclassified_evidence(record),
            rank=len(CLASSIFIERS),
        ),
    )


@dataclass(frozen=True, slots=True)
class TaskResult:
    """One task's record plus its findings. The unit every report row is built from."""

    record: RunRecord
    findings: tuple[Finding, ...] = ()

    @property
    def task_id(self) -> str:
        return self.record.task_id

    @property
    def category(self) -> str:
        return self.record.category

    @property
    def passed(self) -> bool:
        return self.record.passed

    @property
    def skipped(self) -> bool:
        return self.record.skipped

    @property
    def primary(self) -> Finding | None:
        """The highest-ranked finding, or ``None`` for a pass or a skip."""
        return self.findings[0] if self.findings else None

    @property
    def classes(self) -> tuple[FailureClass, ...]:
        return tuple(finding.failure_class for finding in self.findings)


def judge(record: RunRecord) -> TaskResult:
    """Attach :func:`classify`'s findings to ``record``."""
    return TaskResult(record=record, findings=classify(record))


def count_classes(results: Sequence[TaskResult]) -> dict[FailureClass, int]:
    """How many *failures* carry each class. A failure with three classes counts thrice.

    Counting every applicable class rather than only the primary one is deliberate:
    "how often was edit ambiguity part of a failure" is the question that decides
    whether to change the edit tool, and primary-only counting hides it behind
    whatever ranked above it.
    """
    counts: dict[FailureClass, int] = {member: 0 for member in FailureClass}
    for result in results:
        for failure_class in set(result.classes):
            counts[failure_class] += 1
    return counts


__all__ = [
    "BLAME",
    "CLASSIFIERS",
    "EDIT_AMBIGUITY_MARKERS",
    "EDIT_TOOLS",
    "HALLUCINATION_EXCEPTIONS",
    "HEDGE_MARKERS",
    "AgentOutcome",
    "Classifier",
    "FailureClass",
    "Finding",
    "RunRecord",
    "TaskResult",
    "ToolCallRecord",
    "UsageRecord",
    "VerifyProbe",
    "classify",
    "classify_broke_tests",
    "classify_edit_ambiguity",
    "classify_gave_up_early",
    "classify_hallucinated_api",
    "classify_looped",
    "classify_wrong_file",
    "count_classes",
    "failing_checks",
    "fixture_symbols",
    "judge",
    "missing_symbols",
]
