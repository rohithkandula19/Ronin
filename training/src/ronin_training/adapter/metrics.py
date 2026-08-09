"""The adapter's two first-class metrics: tool-syntax validity and recovery rate.

These are the adapter's whole thesis. It is not taught coding knowledge — it is
taught to emit a call Ronin's runtime can parse, and to change its mind after one
fails. So these two numbers, measured the same way against the adapter, the base
model and Kimi, are the honest test of whether the fine-tune did anything.

Both are defined here in one place, precisely, because a metric whose definition
lives in prose next to a table drifts from the code that computed it:

**tool-syntax validity** — of the assistant turns that *attempted* a tool call, the
fraction whose call parses and validates against that tool's JSON schema in Ronin's
registry. A turn that attempted no call is excluded from the denominator and counted
separately (:attr:`SyntaxScore.no_attempt`), because a model that never calls a tool
would otherwise score 100%.

**recovery rate** — of the assistant turns immediately preceded by a *setback* (a
failed ``ToolResult``, or an approval denial from the safety layer), the fraction
that did not repeat the setback's action. Repetition is measured with
``ronin.core.loop.fingerprint``, which is the same identity the runtime's own stall
detector uses, so "repeated" here means exactly what it means there. A turn that
stops calling tools and reports back instead counts as recovered: it adapted.

Every rate is ``float | None``. ``None`` means the denominator was zero — nothing
was measured — and it is never rendered as ``0.0``, because a fabricated zero reads
as a measurement.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

from .config import (
    DIALECT_CLOSE_TAG,
    DIALECT_OPEN_TAG,
    V1_DIALECT_OPEN_TAG,
)

#: Repo-relative location of the generated tool registry (the ground truth for
#: what tools exist and what arguments they take).
REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "tool_registry.json"

#: What to print where a rate has no measurement behind it.
NOT_MEASURED = "—"


class MetricsError(RuntimeError):
    """A metric could not be computed for a reason the caller must fix."""


# --------------------------------------------------------------------------- #
# One turn, as the metrics see it
# --------------------------------------------------------------------------- #


class Setback(StrEnum):
    """The two things a turn can be recovering from.

    Kept as two values rather than one boolean because they fail differently: a
    failed ``ToolResult`` is the tool saying "that did not work", an approval
    denial is a human saying "not that". Reporting them separately is how you tell
    a model that cannot read an error message from one that cannot take a no.
    """

    FAILED_RESULT = "failed_tool_result"
    DENIAL = "approval_denial"


@dataclass(frozen=True, slots=True)
class CallCheck:
    """Whether one attempted tool call was well-formed, and if not, why."""

    ok: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if self.ok and self.reason:
            raise MetricsError("a valid CallCheck cannot carry a reason")
        if not self.ok and not self.reason:
            raise MetricsError(
                "an invalid CallCheck must say why — an unexplained failure cannot "
                "be turned into a taxonomy line later"
            )


VALID_CALL = CallCheck(ok=True)


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One assistant turn, reduced to what the two metrics need.

    Deliberately not the eval suite's report object: the arithmetic must be
    testable against hand-written records with a known error rate, and it must not
    change when the suite's report shape does. The harness converts; this is the
    conversion's target.
    """

    task_id: str
    #: ``None`` when the turn attempted no tool call at all.
    call: CallCheck | None = None
    #: ``ronin.core.loop.fingerprint`` of the attempted call. Required whenever
    #: ``call`` is set, because recovery is measured by comparing fingerprints.
    fingerprint: str = ""
    #: What this turn is responding to, if it is responding to a failure.
    setback: Setback | None = None
    #: The fingerprint of the call that failed or was denied.
    setback_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.task_id:
            raise MetricsError("TurnRecord.task_id must be non-empty")
        if self.call is not None and not self.fingerprint:
            raise MetricsError(
                "a turn that attempted a call must carry its fingerprint — without "
                "one, 'repeated the same action' is unmeasurable"
            )
        if self.setback is not None and not self.setback_fingerprint:
            raise MetricsError(
                "a setback must name the fingerprint of the call that failed or was "
                "denied; otherwise a recovery cannot be distinguished from a repeat"
            )

    @property
    def attempted_call(self) -> bool:
        return self.call is not None

    @property
    def repeated_setback(self) -> bool:
        """Did this turn re-issue the very call that just failed?"""
        return (
            self.setback is not None
            and self.call is not None
            and self.fingerprint == self.setback_fingerprint
        )


# --------------------------------------------------------------------------- #
# Tool-syntax validity
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SyntaxScore:
    """Tool-syntax validity, with its denominator visible.

    ``turns`` and ``no_attempt`` are carried so the rate can never be read without
    the population it came from: 4/4 valid out of 40 turns is a different result
    from 4/4 out of 4.
    """

    turns: int
    attempts: int
    valid: int
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.valid > self.attempts or self.attempts > self.turns:
            raise MetricsError(
                f"impossible SyntaxScore: {self.valid} valid of {self.attempts} "
                f"attempts of {self.turns} turns"
            )

    @property
    def no_attempt(self) -> int:
        return self.turns - self.attempts

    @property
    def invalid(self) -> int:
        return self.attempts - self.valid

    @property
    def rate(self) -> float | None:
        """``valid / attempts``, or ``None`` when nothing attempted a call."""
        return self.valid / self.attempts if self.attempts else None

    def render(self) -> str:
        if self.rate is None:
            return f"{NOT_MEASURED} (0 of {self.turns} turns attempted a tool call)"
        return f"{self.valid}/{self.attempts} = {self.rate:.3f}"


def tool_syntax_validity(turns: Sequence[TurnRecord]) -> SyntaxScore:
    """Count valid calls over attempted calls. Pure arithmetic over classified turns."""
    attempts = 0
    valid = 0
    reasons: list[str] = []
    for turn in turns:
        if turn.call is None:
            continue
        attempts += 1
        if turn.call.ok:
            valid += 1
        else:
            reasons.append(turn.call.reason)
    return SyntaxScore(
        turns=len(turns), attempts=attempts, valid=valid, reasons=tuple(reasons)
    )


# --------------------------------------------------------------------------- #
# Recovery rate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RecoveryScore:
    """Recovery rate, broken out by what was being recovered from."""

    opportunities: int
    recovered: int
    #: ``(setback, recovered, opportunities)`` per setback kind, sorted by kind.
    #: A tuple rather than a mapping so the whole value stays frozen and
    #: comparable in a test.
    breakdown: tuple[tuple[str, int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.recovered > self.opportunities:
            raise MetricsError(
                f"impossible RecoveryScore: {self.recovered} recoveries of "
                f"{self.opportunities} opportunities"
            )

    @property
    def repeated(self) -> int:
        return self.opportunities - self.recovered

    @property
    def rate(self) -> float | None:
        """``recovered / opportunities``, or ``None`` when nothing failed.

        ``None`` here is a real outcome, not an error: a suite in which no tool
        call ever fails and nothing is ever denied measures no recovery, and
        reporting 0.0 or 1.0 for it would both be inventions.
        """
        return self.recovered / self.opportunities if self.opportunities else None

    def render(self) -> str:
        if self.rate is None:
            return f"{NOT_MEASURED} (no failed tool result or denial in the run)"
        return f"{self.recovered}/{self.opportunities} = {self.rate:.3f}"

    def for_setback(self, setback: Setback) -> tuple[int, int] | None:
        for name, recovered, total in self.breakdown:
            if name == setback.value:
                return recovered, total
        return None


def recovery_rate(turns: Sequence[TurnRecord]) -> RecoveryScore:
    """Count adaptations over setbacks.

    A turn recovers when it does *not* re-issue the fingerprint that just failed —
    including by calling nothing at all, which is a legitimate response ("I cannot
    read that file; the path does not exist"). It fails to recover only by
    repeating the exact same action, which is the behaviour the runtime's stall
    detector exists to catch and the behaviour the adapter is trained out of.
    """
    opportunities = 0
    recovered = 0
    per_kind: dict[str, list[int]] = {}
    for turn in turns:
        if turn.setback is None:
            continue
        opportunities += 1
        cell = per_kind.setdefault(turn.setback.value, [0, 0])
        cell[1] += 1
        if not turn.repeated_setback:
            recovered += 1
            cell[0] += 1
    breakdown = tuple(
        (name, cell[0], cell[1]) for name, cell in sorted(per_kind.items())
    )
    return RecoveryScore(
        opportunities=opportunities, recovered=recovered, breakdown=breakdown
    )


# --------------------------------------------------------------------------- #
# Turning raw model output into a CallCheck
# --------------------------------------------------------------------------- #

_BLOCK_RE = re.compile(
    re.escape(DIALECT_OPEN_TAG) + r"\s*(.*?)\s*" + re.escape(DIALECT_CLOSE_TAG),
    re.DOTALL,
)


def find_call_blocks(text: str) -> tuple[str, ...]:
    """Every complete ``<ronin:tool_call>`` payload in ``text``, in order.

    An *incomplete* block (open tag, no close tag) is not returned here — it is
    detected by :func:`check_raw_call`, which needs to report it as a specific
    syntax failure rather than as "no call attempted". The distinction is the
    difference between a model that stayed silent and one that truncated.
    """
    return tuple(match.group(1) for match in _BLOCK_RE.finditer(text))


def attempted_call(text: str) -> bool:
    """Whether ``text`` tried to call a tool, well-formed or not.

    A **v1** ``<tool_call>`` block counts as an attempt even though the v2 runtime
    will never execute it. Treating it as "no call attempted" would drop the turn out
    of the denominator, and a model emitting the wrong dialect throughout would score
    an unmeasured ``—`` instead of a very loud zero. The two tags cannot be confused
    by substring: ``<ronin:tool_call>`` does not contain ``<tool_call>``.
    """
    return DIALECT_OPEN_TAG in text or V1_DIALECT_OPEN_TAG in text


def check_raw_call(text: str, *, schemas: Mapping[str, Mapping[str, Any]]) -> CallCheck:
    """Validate the first tool call in a raw assistant message.

    Every failure mode gets its own reason string, because the taxonomy question
    ("fix the harness or fix the model?") is answered by *which* of these it is:
    a missing close tag is a decoding problem, an unknown tool name is a prompt
    problem, a schema violation is a training problem.
    """
    if not attempted_call(text):
        raise MetricsError(
            "check_raw_call was given text with no tool-call attempt; use "
            "attempted_call() first so a silent turn is not scored as a bad call"
        )
    if DIALECT_OPEN_TAG not in text:
        # Only the v1 tag is present. This is the single highest-severity outcome the
        # metric can report and it must not look like anything else: the corpus and
        # the runtime disagree about the dialect, so every call fails for one reason.
        return CallCheck(
            ok=False,
            reason=(
                f"emitted the v1 {V1_DIALECT_OPEN_TAG} dialect; Ronin's format shim "
                f"parses {DIALECT_OPEN_TAG} and only that — the training corpus and "
                "the runtime disagree, which is a data bug, not a training one"
            ),
        )
    blocks = find_call_blocks(text)
    if not blocks:
        return CallCheck(
            ok=False,
            reason=f"opened {DIALECT_OPEN_TAG} and never emitted {DIALECT_CLOSE_TAG}",
        )
    payload = blocks[0]
    try:
        parsed = json.loads(payload)
    except ValueError as exc:
        return CallCheck(ok=False, reason=f"call payload is not valid JSON: {exc}")
    if not isinstance(parsed, Mapping):
        return CallCheck(
            ok=False, reason=f"call payload is a {type(parsed).__name__}, not an object"
        )
    name = parsed.get("name")
    if not isinstance(name, str) or not name:
        return CallCheck(ok=False, reason="call payload has no string 'name'")
    if "arguments" not in parsed:
        return CallCheck(ok=False, reason=f"call to {name!r} has no 'arguments' key")
    return check_call(name, parsed["arguments"], schemas=schemas)


def check_call(
    name: str, arguments: object, *, schemas: Mapping[str, Mapping[str, Any]]
) -> CallCheck:
    """Validate one call's name and arguments against the registry's JSON schema.

    ``jsonschema`` is imported here rather than at module scope: it is a declared
    dependency of this package, but the dataclasses above must stay importable
    without it so a config can be inspected on a bare install.
    """
    schema = schemas.get(name)
    if schema is None:
        return CallCheck(
            ok=False,
            reason=f"unknown tool {name!r} (not in Ronin's registry: {sorted(schemas)})",
        )
    if isinstance(arguments, str):
        # A JSON *string* where an object belongs is the single most common
        # dialect error, and it is worth naming rather than folding into
        # "violates its schema".
        return CallCheck(
            ok=False,
            reason=f"call to {name!r} passes arguments as a JSON string, not an object",
        )
    if not isinstance(arguments, Mapping):
        return CallCheck(
            ok=False,
            reason=(
                f"call to {name!r} passes arguments as a "
                f"{type(arguments).__name__}, not an object"
            ),
        )
    try:
        import jsonschema
    except ImportError as exc:
        raise MetricsError(
            "tool-syntax validity needs jsonschema (a declared dependency of "
            "ronin-training: `uv pip install jsonschema`) — it validates arguments "
            "against the real registry, and skipping it would inflate the metric"
        ) from exc
    try:
        jsonschema.validate(dict(arguments), dict(schema))
    except jsonschema.ValidationError as exc:
        return CallCheck(
            ok=False, reason=f"call to {name!r} violates its schema: {exc.message}"
        )
    return VALID_CALL


@cache
def load_tool_schemas(path: str | None = None) -> Mapping[str, Mapping[str, Any]]:
    """``tool name → JSON schema`` from the generated registry.

    Cached because the three-way run validates thousands of calls against it and
    it never changes mid-run.
    """
    file = Path(path) if path is not None else REGISTRY_PATH
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MetricsError(
            f"cannot read the tool registry at {file}: {exc} — regenerate it with "
            "`python -m ronin_training.tool_registry_loader`"
        ) from exc
    tools = data.get("tools")
    if not isinstance(tools, list):
        raise MetricsError(f"{file} has no 'tools' list")
    out: dict[str, Mapping[str, Any]] = {}
    for tool in tools:
        if not isinstance(tool, Mapping) or "name" not in tool:
            raise MetricsError(f"{file} contains a tool entry with no name")
        out[str(tool["name"])] = dict(tool.get("parameters") or {})
    return out


def call_fingerprint(name: str, arguments: Mapping[str, Any]) -> str:
    """``ronin.core.loop.fingerprint`` for a call, without a hard dependency on it.

    Imported lazily and called for real rather than reimplemented: the runtime's
    stall detector and this metric must agree on what "the same action" is, and
    two copies of that normalization drift.
    """
    try:
        from ronin.core.loop import fingerprint
        from ronin.core.types import ToolUse
    except ImportError as exc:
        raise MetricsError(
            "recovery rate needs ronin.core.loop.fingerprint, which means the "
            "`ronin` package must be importable (PYTHONPATH=src from the repo root, "
            "or `pip install -e .`) — reimplementing the fingerprint here would let "
            "the metric and the runtime's stall detector disagree"
        ) from exc
    return fingerprint(ToolUse(id="metric", name=name, arguments=dict(arguments)))
