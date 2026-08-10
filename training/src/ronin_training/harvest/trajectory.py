"""The trajectory value the whole pipeline reads: a run, step by step, as data.

Deliberately **pure and self-contained** — no import of ``ronin.*``. The training
package installs on a bare Colab runtime where only ``training/`` is on the path
(see ``training/FREE_TRAINING.md``), so a hard dependency on the agent tree would
make the harvester unimportable exactly where it has to run. Conversion from the
real recorded forms lives in :mod:`.sources`, behind a lazy import.

The price of that separation is :func:`fingerprint`, which restates the rule from
``ronin.core.loop.fingerprint``. Two implementations that merely *happen* to agree
is the failure this repo already paid for once (see
``training/reports/valid_tool_json_root_cause.md``, divergence D3), so the copy is
pinned by a parity test that imports the real one and fails the moment it drifts.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .provenance import SourceKind

#: How ``ronin.core.loop`` renders a refused call into the observation the model
#: reads (``loop.py``: ``ToolResult(ok=False, error=f"DENIED: {detail}")``). The
#: gate's feedback is everything after this prefix, which is what the model is
#: supposed to adapt to and what the ``ignored_denial`` negative proves it did not.
DENIAL_PREFIX = "DENIED: "


def fingerprint(name: str, arguments: Mapping[str, Any]) -> str:
    """A stable identity for "the same action" — the loop's stall-detection rule.

    Arguments are key-sorted so ``{"a":1,"b":2}`` and ``{"b":2,"a":1}`` are one
    action. Unserializable values fall back to ``repr``, stable within a process.
    """
    try:
        args = json.dumps(dict(arguments), sort_keys=True, default=repr)
    except (TypeError, ValueError):  # pragma: no cover - default=repr covers this
        args = repr(sorted(arguments.items()))
    return f"{name}:{args}"


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """One tool as the trajectory's own toolset declared it.

    Carried on the trajectory rather than looked up globally because the toolset
    is a property of the *run*: Ronin v1 named its file reader ``read_file`` and
    v2 names it ``read``, so a harvester that hardcoded either one would render a
    prompt the run was never given.
    """

    name: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolSchema.name is required")
        if not self.description:
            raise ValueError(
                f"tool {self.name!r} has no description — the description is the "
                "only thing the model has to decide whether to call it, so a row "
                "rendered without one trains a different prompt than inference sends"
            )


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """One tool call the model actually made, as the transcript recorded it."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("RecordedCall.id is required")
        if not self.name:
            raise ValueError("RecordedCall.name is required")

    def fingerprint(self) -> str:
        return fingerprint(self.name, self.arguments)


@dataclass(frozen=True, slots=True)
class RecordedResult:
    """What the harness handed back for one call. Failure is a value, as in Ronin."""

    call_id: str
    name: str
    ok: bool
    content: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("RecordedResult.call_id is required")

    @property
    def denied(self) -> bool:
        """Whether the approval gate refused this call (as opposed to it failing)."""
        return not self.ok and self.error.startswith(DENIAL_PREFIX)

    @property
    def denial_reason(self) -> str:
        """The gate's feedback, without the marker. Empty when not a denial."""
        return self.error[len(DENIAL_PREFIX) :] if self.denied else ""

    def observation(self) -> str:
        """The text the model saw for this call — content on success, error on failure."""
        return self.content if self.ok else self.error


@dataclass(frozen=True, slots=True)
class Step:
    """One assistant turn and the observations it earned.

    ``text`` is the model's prose *as emitted*, including any malformed
    ``<tool_call>`` block the harness could not parse. Keeping the unparsed text
    is what makes the ``malformed_tool_json`` negative minable at all — a
    representation that stored only successfully-parsed calls would have thrown
    away every example of the failure it most needs to teach against.
    """

    index: int
    text: str = ""
    calls: tuple[RecordedCall, ...] = ()
    results: tuple[RecordedResult, ...] = ()

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("Step.index must be >= 0")

    def result_for(self, call_id: str) -> RecordedResult | None:
        return next((r for r in self.results if r.call_id == call_id), None)

    def call_by_id(self, call_id: str) -> RecordedCall | None:
        return next((c for c in self.calls if c.id == call_id), None)


@dataclass(frozen=True, slots=True)
class Trajectory:
    """One run of one task, with the outcome that decides whether it may teach.

    ``verified`` is the ``verify.sh`` result *as recorded by whoever ran it*. The
    harvester never runs a verification itself: a pipeline that could decide its
    own ground truth would drift from the eval suite the moment either changed.
    Unrecorded therefore means ``False``, and the filter drops the run — the safe
    direction, because a bad-but-passing run teaches bad habits and an
    unknown-outcome run teaches unknown ones.
    """

    task_id: str
    run_id: str
    prompt: str
    model: str = ""
    system: str = ""
    tools: tuple[ToolSchema, ...] = ()
    steps: tuple[Step, ...] = ()
    verified: bool = False
    source: SourceKind = SourceKind.EVAL_SUITE

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("Trajectory.task_id is required")
        if not self.run_id:
            raise ValueError("Trajectory.run_id is required")
        if not self.prompt:
            raise ValueError(
                "Trajectory.prompt is required — an example whose user turn is "
                "empty trains the model to act with no instruction"
            )
        if not isinstance(self.steps, tuple):
            raise TypeError("Trajectory.steps must be a tuple (frozen)")

    @property
    def turn_count(self) -> int:
        """Assistant turns. The unit the 1.5x-median filter is expressed in."""
        return len(self.steps)

    def tool_named(self, name: str) -> ToolSchema | None:
        return next((t for t in self.tools if t.name == name), None)

    def calls(self) -> tuple[tuple[Step, RecordedCall], ...]:
        """Every call in order, paired with the step that made it."""
        return tuple((step, call) for step in self.steps for call in step.calls)


__all__ = [
    "DENIAL_PREFIX",
    "RecordedCall",
    "RecordedResult",
    "Step",
    "ToolSchema",
    "Trajectory",
    "fingerprint",
]
