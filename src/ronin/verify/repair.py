"""Try to fix a failing verify pass, and stop lying about it after three tries.

The loop must not end because the model said "done", and it must not run forever
because the model said "let me try once more". Both failures have the same cure:
a *signature* for "the same failure", so the third identical result is provably
identical and the loop can stop with a report instead of an apology.

Normalization is the crux, and it is where a naive implementation quietly breaks.
Two runs of the same broken test differ in ways that have nothing to do with the
bug: the assertion moved three lines down because the model added an import, the
tmpdir is ``pytest-of-user/pytest-41`` this time, the object repr carries a new
heap address, the suite took 0.09s instead of 0.11s, and ``-p no:randomly`` was
not passed so the failures came back in a different order. A signature that sees
any of that as change will happily loop until the attempt cap, report "making
progress", and then hand the user three near-identical diffs. So every one of
those is stripped, and the stripping is tested against real pytest and mypy
output that differs *only* in noise.

The honest path is a value, not an exception: :class:`RepairReport` carries every
attempt, every diff tried, and the stable signature, and it renders as the
sentence a good engineer says out loud — "i could not fix this, here is what i
tried and what i think is wrong". An agent that admits failure is worth more than
one that loops, so admitting it is a first-class return type.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from .runner import VerifyOutcome

DEFAULT_MAX_ATTEMPTS: Final[int] = 3

#: How many identical signatures mean "stuck". Three, because two can be a
#: coincidence of a partial fix and the count is what the work order specifies.
IDENTICAL_LIMIT: Final[int] = 3

# --------------------------------------------------------------------------- #
# Noise stripping
# --------------------------------------------------------------------------- #

#: Each rule replaces one class of run-to-run noise with a fixed token. Order
#: matters: temp paths are collapsed before line numbers, or the digits inside
#: ``pytest-41`` get rewritten first and the path stops matching.
NOISE_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # tmpdirs, in the shapes pytest and tempfile produce on linux and macos
    (re.compile(r"/private/var/folders/[^\s:'\"]+"), "<tmp>"),
    (re.compile(r"/var/folders/[^\s:'\"]+"), "<tmp>"),
    (re.compile(r"/tmp/[^\s:'\"]+"), "<tmp>"),
    (re.compile(r"[A-Za-z]:\\\\?(?:[^\\\s]+\\\\?)*?Temp\\\\?[^\s:'\"]*"), "<tmp>"),
    # heap addresses in reprs: `<Thing object at 0x7f3c9a1b2d30>`
    (re.compile(r"0x[0-9a-fA-F]{4,}"), "0xADDR"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{40}\b"), "<sha>"),
    # durations: `in 0.12s`, `(0.03 s)`, `took 1.5 seconds`, `12ms`
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:s|ms|sec|secs|seconds?|minutes?)\b"), "<dur>"),
    # `file.py:123:` and `file.py:123:45:` — the line moves on every edit above it
    (re.compile(r"(?<=[\w/\\.\-])\.py:\d+(?::\d+)?"), ".py:L"),
    (re.compile(r"(?<=[\w/\\.\-])\.(ts|tsx|js|jsx|rs|go):\d+(?::\d+)?"), r".\1:L"),
    # pytest's own `line 123, in frame`
    (re.compile(r"\bline \d+\b"), "line L"),
    # worker ids from xdist, which shuffle between runs
    (re.compile(r"\[gw\d+\]"), "[gw]"),
    (re.compile(r"[ \t]+"), " "),
)

#: Lines that are pure run bookkeeping. They contain counts we *do* want (see
#: :func:`failure_count`) but including them in a signature makes every run
#: unique the moment a single unrelated test is added.
_BANNER = re.compile(
    r"^(=+.*=+|-+.*-+|_+.*_+|platform \w+|rootdir:|plugins:|configfile:|"
    r"collected \d+|collecting|cachedir:|Found \d+ errors?|Success: no issues)"
)

#: A source location *after* normalization: the line number is already gone, so
#: the patterns below have to accept the ``L`` token as well as raw digits (they
#: are also used directly on un-normalized text by callers' tests).
_LOC = r"(?:L|\d+(?::\d+)?)"

_PYTEST_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$")
_PYTEST_HEADER = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}$")
_PYTEST_ASSERT = re.compile(r"^E\s+(.*)$")
_RUFF = re.compile(rf"^(?P<file>[^\s:]+):{_LOC}:\s*(?P<code>[A-Z]{{1,4}}\d{{3,4}})\s+(?P<msg>.*)$")
_MYPY = re.compile(rf"^(?P<file>[^\s:]+):{_LOC}:\s*error:\s*(?P<msg>.*)$")
_PYTEST_SUMMARY = re.compile(r"\b(\d+) failed\b")
_PYTEST_ERRORS = re.compile(r"\b(\d+) errors?\b")


def normalize_line(line: str) -> str:
    """Strip every class of run-to-run noise from one line."""
    text = line.rstrip()
    for pattern, replacement in NOISE_RULES:
        text = pattern.sub(replacement, text)
    return text.strip()


class FailureKind(StrEnum):
    TEST = "test"
    TYPE = "type"
    LINT = "lint"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Failure:
    """One failing thing, identified by *what* failed rather than *where*.

    ``where`` keeps the file (a real identity) and drops the line number (which
    moves whenever anything above it is edited). ``detail`` is the normalized
    message — the assertion shape, not the assertion's coordinates.
    """

    kind: FailureKind
    where: str
    detail: str

    @property
    def token(self) -> str:
        return f"{self.kind.value}|{self.where}|{self.detail}"


def parse_failures(output: str) -> tuple[Failure, ...]:
    """Extract the individual failures from mixed tool output.

    Handles the three shapes that matter: pytest's short summary
    (``FAILED path::test - AssertionError: ...``), pytest's per-test section
    header plus its ``E   ...`` assertion lines, and the
    ``file:line: error: message`` form shared by mypy and ruff. Anything else
    becomes a single :attr:`FailureKind.OTHER` failure so an unrecognized failure
    still has a stable identity rather than being silently dropped.
    """
    failures: list[Failure] = []
    seen: set[str] = set()

    def add(failure: Failure) -> None:
        if failure.token not in seen:
            seen.add(failure.token)
            failures.append(failure)

    current_test: str | None = None

    for raw in output.splitlines():
        line = normalize_line(raw)
        if not line:
            continue

        header = _PYTEST_HEADER.match(line)
        if header is not None:
            current_test = header.group(1).strip()
            continue

        summary = _PYTEST_FAILED.match(line)
        if summary is not None:
            add(
                Failure(
                    kind=FailureKind.TEST,
                    where=summary.group(1),
                    detail=(summary.group(2) or "").strip(),
                )
            )
            continue

        assertion = _PYTEST_ASSERT.match(line)
        if assertion is not None and current_test is not None:
            add(
                Failure(
                    kind=FailureKind.TEST,
                    where=current_test,
                    detail=assertion.group(1).strip(),
                )
            )
            continue

        lint = _RUFF.match(line)
        if lint is not None:
            add(
                Failure(
                    kind=FailureKind.LINT,
                    where=lint.group("file"),
                    detail=f"{lint.group('code')} {lint.group('msg')}".strip(),
                )
            )
            continue

        typed = _MYPY.match(line)
        if typed is not None:
            add(
                Failure(
                    kind=FailureKind.TYPE,
                    where=typed.group("file"),
                    detail=typed.group("msg").strip(),
                )
            )
            continue

    if failures:
        return _collapse(failures)

    normalized = (normalize_line(raw) for raw in output.splitlines())
    meaningful = [line for line in normalized if line and not _BANNER.match(line)]
    if not meaningful:
        return ()
    return (
        Failure(
            kind=FailureKind.OTHER,
            where="unparsed",
            detail=" ".join(meaningful[-6:]),
        ),
    )


def _collapse(failures: list[Failure]) -> tuple[Failure, ...]:
    """One record per failing test; type and lint errors stay one-per-message.

    pytest names the same failure twice — once in the ``____ test_x ____`` section
    with its ``E`` assertion lines, once in the short summary as
    ``FAILED path::test_x`` — and counting both makes "3 failing" read as 6, which
    breaks the progress comparison the repair loop runs on. Type and lint output
    is the opposite: several distinct errors legitimately share one file, so
    collapsing by location there would hide failures.
    """
    full_ids = {
        failure.where
        for failure in failures
        if failure.kind is FailureKind.TEST and "::" in failure.where
    }
    best: dict[str, Failure] = {}
    others: list[Failure] = []
    for failure in failures:
        if failure.kind is not FailureKind.TEST:
            others.append(failure)
            continue
        where = _canonical_test_id(failure.where, full_ids)
        current = best.get(where)
        if current is None or len(failure.detail) > len(current.detail):
            best[where] = replace(failure, where=where)
    return (*best.values(), *others)


def _canonical_test_id(where: str, full_ids: set[str]) -> str:
    """Map a section-header name onto the full ``path::test`` id when one exists.

    ``____ TestThing.test_x ____`` and ``FAILED tests/t.py::TestThing::test_x`` are
    the same failure, and only the second form is unambiguous.
    """
    if where in full_ids:
        return where
    suffix = where.replace(".", "::")
    for candidate in full_ids:
        if candidate.endswith(f"::{suffix}") or candidate.endswith(f"::{where}"):
            return candidate
    return where


def failure_signature(output: str) -> str:
    """A stable id for "this exact set of failures".

    Sorted, so a test runner that shuffles order produces the same signature, and
    hashed so the value is short enough to compare in a log line. Empty output
    (or output with nothing failure-shaped in it) hashes to a distinct constant
    rather than the empty string, so "no failures" cannot collide with "not run".
    """
    failures = parse_failures(output)
    if not failures:
        return "none"
    joined = "\n".join(sorted(failure.token for failure in failures))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def failure_count(output: str) -> int:
    """How many things are failing, preferring the runner's own count.

    pytest's ``3 failed`` is authoritative — it knows about failures whose output
    we could not parse. The parsed count is the fallback, and both being present
    and disagreeing is normal (a collection error is an "error", not a "failure").
    """
    reported = 0
    for pattern in (_PYTEST_SUMMARY, _PYTEST_ERRORS):
        for match in pattern.finditer(output):
            reported += int(match.group(1))
    if reported:
        return reported
    return len(parse_failures(output))


# --------------------------------------------------------------------------- #
# Snapshots and attempts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FailureSnapshot:
    """One verify pass, reduced to what the repair loop reasons about."""

    ok: bool
    output: str
    signature: str
    count: int
    failures: tuple[Failure, ...] = ()

    @classmethod
    def from_output(cls, output: str, *, ok: bool) -> FailureSnapshot:
        return cls(
            ok=ok,
            output=output,
            signature="none" if ok else failure_signature(output),
            count=0 if ok else failure_count(output),
            failures=() if ok else parse_failures(output),
        )

    @classmethod
    def from_outcome(cls, outcome: VerifyOutcome) -> FailureSnapshot:
        """Bridge from the runner. ``unknown`` counts as not-ok on purpose.

        A pass with nothing to run proves nothing, and treating it as success is
        exactly the "the model said done" failure this package exists to prevent.
        """
        return cls.from_output(outcome.failure_output, ok=outcome.ok)


@dataclass(frozen=True, slots=True)
class Attempt:
    """One repair attempt: what was changed, and what happened afterwards."""

    index: int
    diff: str
    snapshot: FailureSnapshot
    note: str = ""

    @property
    def fixed(self) -> bool:
        return self.snapshot.ok


class RepairVerdict(StrEnum):
    """How the repair loop ended. Every value is reachable and separately tested."""

    ALREADY_GREEN = "already_green"
    FIXED = "fixed"
    STUCK = "stuck"
    EXHAUSTED = "exhausted"
    REGRESSED = "regressed"


@dataclass(frozen=True, slots=True)
class RepairReport:
    """The structured account of a repair loop, honest ending included."""

    verdict: RepairVerdict
    attempts: tuple[Attempt, ...]
    baseline: FailureSnapshot
    stable_signature: str = ""
    diagnosis: str = ""
    #: How many *runs* produced :attr:`stable_signature`, counting the failing run
    #: that started the loop. The stop rule is stated in these terms because "the
    #: same test failed identically three times" is three observations, which is
    #: two wasted edits — stopping there is one edit cheaper than waiting for a
    #: third attempt to tell us what we already knew.
    identical_seen: int = 0

    @property
    def fixed(self) -> bool:
        return self.verdict in {RepairVerdict.ALREADY_GREEN, RepairVerdict.FIXED}

    @property
    def final(self) -> FailureSnapshot:
        return self.attempts[-1].snapshot if self.attempts else self.baseline

    def render(self) -> str:
        """The human-facing report. Says "i could not fix this" when that is true."""
        if self.verdict is RepairVerdict.ALREADY_GREEN:
            return "verification was already green; nothing to repair."
        if self.verdict is RepairVerdict.FIXED:
            return (
                f"fixed after {len(self.attempts)} attempt(s). "
                f"the failure was {self.baseline.signature} "
                f"({self.baseline.count} failing)."
            )

        lines = [
            "i could not fix this.",
            "",
            f"what failed: {self.baseline.count} failing, signature {self.baseline.signature}",
        ]
        for failure in self.baseline.failures[:5]:
            lines.append(f"  - {failure.kind.value}: {failure.where} — {failure.detail}")
        if len(self.baseline.failures) > 5:
            lines.append(f"  … and {len(self.baseline.failures) - 5} more")

        lines.append("")
        lines.append(f"what i tried ({len(self.attempts)} attempt(s)):")
        for attempt in self.attempts:
            change = attempt.diff.strip() or "(no change was produced)"
            first = change.splitlines()[0] if change.splitlines() else change
            lines.append(
                f"  {attempt.index}. {first}"
                f"  → {attempt.snapshot.count} failing, "
                f"signature {attempt.snapshot.signature}"
            )
            if attempt.note:
                lines.append(f"     note: {attempt.note}")

        if self.verdict is RepairVerdict.STUCK:
            lines.append(
                f"     (that signature has now been seen {self.identical_seen} times, "
                "counting the run before the first edit — that is the stop rule)"
            )

        lines.append("")
        lines.append(f"what i think is wrong: {self.diagnosis}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #

#: The verify seam: run the checks and reduce them to a snapshot.
VerifyStep = Callable[[], Awaitable[FailureSnapshot]]
#: The model seam: given the current failure and the attempt number, change
#: something and return the diff that was applied ("" if nothing was).
FixStep = Callable[[FailureSnapshot, int], Awaitable[str]]

_STUCK_DIAGNOSIS = (
    "the same failure came back unchanged after {n} edits, so the edits are not "
    "reaching it. either the failing assertion is asserting something the code was "
    "never asked to do, or the real cause is somewhere i did not look — a fixture, a "
    "cached artifact, or a different module than the one the traceback names."
)
_EXHAUSTED_DIAGNOSIS = (
    "the failure changed with each edit but never cleared inside the {n}-attempt cap, "
    "so i was moving without converging. the remaining failure is the one to look at "
    "first; my last edit is the most likely thing to review or revert."
)
_REGRESSED_DIAGNOSIS = (
    "i made it worse: {before} failing before, {after} after. the last edit should be "
    "reverted before anything else is tried."
)


async def repair_loop(
    *,
    verify: VerifyStep,
    fix: FixStep,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    identical_limit: int = IDENTICAL_LIMIT,
) -> RepairReport:
    """Attempt repairs under a hard cap, stopping early on stuck or on green.

    Progress is judged on two axes, because they mean different things:

    * the **signature** changing means the edits are reaching the failure;
    * the **count** dropping means they are reaching it in the right direction.

    Same signature ``identical_limit`` times → stop and report (:attr:`STUCK`);
    the loop cannot learn anything more by repeating itself. Signature changing
    but the count rising is recorded per attempt and, if the loop ends that way,
    reported as :attr:`REGRESSED` — "i made it worse" is information the user
    needs and the model will not volunteer.
    """
    if max_attempts < 1:
        raise ValueError("repair_loop needs at least one attempt to be worth calling")

    baseline = await verify()
    if baseline.ok:
        return RepairReport(verdict=RepairVerdict.ALREADY_GREEN, attempts=(), baseline=baseline)

    attempts: list[Attempt] = []
    seen: dict[str, int] = {baseline.signature: 1}
    previous = baseline

    for index in range(1, max_attempts + 1):
        diff = await fix(previous, index)
        snapshot = await verify()
        note = _progress_note(previous, snapshot)
        attempts.append(Attempt(index=index, diff=diff, snapshot=snapshot, note=note))

        if snapshot.ok:
            return RepairReport(
                verdict=RepairVerdict.FIXED,
                attempts=tuple(attempts),
                baseline=baseline,
                stable_signature="",
                diagnosis="",
            )

        seen[snapshot.signature] = seen.get(snapshot.signature, 0) + 1
        if seen[snapshot.signature] >= identical_limit:
            return RepairReport(
                verdict=RepairVerdict.STUCK,
                attempts=tuple(attempts),
                baseline=baseline,
                stable_signature=snapshot.signature,
                diagnosis=_STUCK_DIAGNOSIS.format(n=len(attempts)),
                identical_seen=seen[snapshot.signature],
            )
        previous = snapshot

    final = attempts[-1].snapshot
    if final.count > baseline.count:
        return RepairReport(
            verdict=RepairVerdict.REGRESSED,
            attempts=tuple(attempts),
            baseline=baseline,
            stable_signature=final.signature,
            diagnosis=_REGRESSED_DIAGNOSIS.format(before=baseline.count, after=final.count),
        )
    return RepairReport(
        verdict=RepairVerdict.EXHAUSTED,
        attempts=tuple(attempts),
        baseline=baseline,
        stable_signature=final.signature,
        diagnosis=_EXHAUSTED_DIAGNOSIS.format(n=max_attempts),
    )


def _progress_note(before: FailureSnapshot, after: FailureSnapshot) -> str:
    if after.ok:
        return "everything passed"
    if after.signature == before.signature:
        return "identical failure — that edit did not reach it"
    if after.count < before.count:
        return f"different failure, fewer of them ({before.count} → {after.count})"
    if after.count > before.count:
        return f"different failure and MORE of them ({before.count} → {after.count})"
    return "different failure, same count"


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "IDENTICAL_LIMIT",
    "NOISE_RULES",
    "Attempt",
    "Failure",
    "FailureKind",
    "FailureSnapshot",
    "FixStep",
    "RepairReport",
    "RepairVerdict",
    "VerifyStep",
    "failure_count",
    "failure_signature",
    "normalize_line",
    "parse_failures",
    "repair_loop",
]
