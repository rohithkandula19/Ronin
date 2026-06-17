"""Wire the faithfulness / grounding harness into the agent loop and edits.

The harness core lives in ``ronin_hardening.faithfulness`` (claim decomposition,
grounding, hallucinated-symbol detection, a calibrated 0..1 score, and an
abstain verdict). This module is the thin CLI-side wiring that:

1. Pulls the sources the agent actually observed out of its execution trace.
2. Runs the harness on the agent's finished answer (a *post-answer hook*).
3. Runs the harness on a proposed file edit's new code (an *edit/write guard*),
   flagging writes that reference symbols absent from everything the agent read.
4. Renders the result for the terminal and decides, by mode, whether to warn or
   hold for confirmation.

It does not re-implement any grounding logic; it reuses the hardening package so
the same harness powers the CLI, the ``ronin faithfulness check`` command, and
the ``--faithfulness`` flag on ``ronin agent``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ronin_hardening import (
    FaithfulnessHarness,
    FaithfulnessReport,
    Source,
    sources_from_trace,
)

# Modes, mirroring RoninConfig.faithfulness.
MODE_OFF = "off"
MODE_WARN = "warn"
MODE_GATE = "gate"
VALID_MODES = (MODE_OFF, MODE_WARN, MODE_GATE)

# When the calibrated score is at or below this, the answer is treated as
# ungrounded (independent of the harness's own uncertain band). Kept a touch
# below the abstain band so a clean abstain is reported as "uncertain", not
# "ungrounded".
UNGROUNDED_SCORE = 0.45


@dataclass
class FaithfulnessOutcome:
    """The result of evaluating an answer/edit plus the action a host should take."""

    report: FaithfulnessReport
    mode: str
    # True when the harness found a real grounding problem (low score or a
    # fabricated symbol). Distinct from ``abstain`` (not enough evidence to judge).
    ungrounded: bool

    @property
    def abstain(self) -> bool:
        return self.report.abstain

    @property
    def should_warn(self) -> bool:
        """Surface a warning in warn or gate mode when there is something to say."""
        return self.mode in (MODE_WARN, MODE_GATE) and (
            self.ungrounded or self.abstain
        )

    @property
    def should_gate(self) -> bool:
        """Hold for confirmation only in gate mode, and only on a real problem."""
        return self.mode == MODE_GATE and (self.ungrounded or self.abstain)

    @property
    def summary(self) -> str:
        """One-line plain-text summary (ASCII only)."""
        r = self.report
        if r.abstain:
            return f"faithfulness: ABSTAIN (score {r.score:.2f}) - {r.reason}"
        verdict = "ungrounded" if self.ungrounded else "grounded"
        n_bad = len(r.ungrounded_claims)
        n_halluc = len(r.hallucinated_symbols)
        bits = [f"faithfulness: {verdict} (score {r.score:.2f})"]
        if n_bad:
            bits.append(f"{n_bad} ungrounded claim(s)")
        if n_halluc:
            bits.append(f"{n_halluc} hallucinated symbol(s)")
        return "; ".join(bits)


def _harness(config: Any | None) -> FaithfulnessHarness:
    """Build a harness. Kept config-aware so a future setting can tune thresholds,
    but defaults are the calibrated ones from the hardening package."""
    return FaithfulnessHarness()


def mode_of(config: Any | None) -> str:
    """Resolve the faithfulness mode from a config object, defaulting to off."""
    mode = getattr(config, "faithfulness", MODE_OFF) or MODE_OFF
    mode = str(mode).strip().lower()
    return mode if mode in VALID_MODES else MODE_OFF


def evaluate_answer(
    answer: str,
    sources: list[Source],
    *,
    mode: str = MODE_WARN,
    config: Any | None = None,
) -> FaithfulnessOutcome:
    """Post-answer hook: score ``answer`` against the ``sources`` the agent read.

    Returns a :class:`FaithfulnessOutcome` describing whether to warn or gate. The
    grounding signal is computed by the real harness; a stubbed "always grounded"
    implementation would not separate grounded from ungrounded answers.
    """
    report = _harness(config).check(answer, sources)
    ungrounded = _is_ungrounded(report)
    return FaithfulnessOutcome(report=report, mode=mode, ungrounded=ungrounded)


def evaluate_answer_from_trace(
    answer: str,
    trace: list[Any],
    *,
    mode: str = MODE_WARN,
    include_all_tools: bool = False,
    config: Any | None = None,
) -> FaithfulnessOutcome:
    """Post-answer hook driven straight off an ``AgentResult.trace``.

    Pulls the observed sources out of the trace (files read, search/list/fetch
    results; the agent's own writes and errored results are excluded) and scores
    the answer against them.
    """
    sources = sources_from_trace(trace, include_all_tools=include_all_tools)
    return evaluate_answer(answer, sources, mode=mode, config=config)


def evaluate_edit(
    new_code: str,
    sources: list[Source],
    *,
    mode: str = MODE_WARN,
    config: Any | None = None,
) -> FaithfulnessOutcome:
    """Edit/write guard: score a proposed edit's new code against read sources.

    Trips primarily on hallucinated symbols: a write that calls a helper or
    references a file the agent never opened is the classic ungrounded edit.
    """
    report = _harness(config).check_edit(new_code, sources)
    ungrounded = _is_ungrounded(report)
    return FaithfulnessOutcome(report=report, mode=mode, ungrounded=ungrounded)


def evaluate_edit_from_trace(
    new_code: str,
    trace: list[Any],
    *,
    mode: str = MODE_WARN,
    include_all_tools: bool = False,
    config: Any | None = None,
) -> FaithfulnessOutcome:
    """Edit/write guard driven off an ``AgentResult.trace``."""
    sources = sources_from_trace(trace, include_all_tools=include_all_tools)
    return evaluate_edit(new_code, sources, mode=mode, config=config)


def _is_ungrounded(report: FaithfulnessReport) -> bool:
    """A real grounding problem: any hallucinated symbol, or a firmly low score.

    Abstain takes precedence: it means "not enough evidence to judge" (e.g. zero
    sources, where *every* symbol looks unsupported). We report that as abstain,
    not as ungrounded, so the two verdicts stay distinct.
    """
    if report.abstain:
        return False
    if report.hallucinated_symbols:
        return True
    return report.score <= UNGROUNDED_SCORE


# ---------------------------------------------------------------------------
# Rendering (Rich-aware, but degrades to plain text without a console)
# ---------------------------------------------------------------------------


def render(outcome: FaithfulnessOutcome, console: Any | None = None) -> str:
    """Render the outcome. Prints a Rich panel when a console is given; always
    returns the plain-text summary so callers (and tests) can assert on it."""
    text = outcome.summary
    if console is None:
        return text

    try:
        from rich.panel import Panel
    except Exception:  # noqa: BLE001 - rendering must never break the run
        console.print(text)
        return text

    r = outcome.report
    if r.abstain:
        style, title = "yellow", "faithfulness: abstain"
    elif outcome.ungrounded:
        style, title = "red", "faithfulness: ungrounded"
    else:
        style, title = "green", "faithfulness: grounded"

    lines = [f"score: [bold]{r.score:.2f}[/bold]   sources: {r.n_sources}"]
    if r.abstain:
        lines.append(f"reason: {r.reason}")
    if r.hallucinated_symbols:
        lines.append("hallucinated symbols: " + ", ".join(r.hallucinated_symbols))
    for v in r.ungrounded_claims[:5]:
        claim = v.claim if len(v.claim) <= 100 else v.claim[:97] + "..."
        lines.append(f"  - ungrounded: {claim}")
    console.print(Panel("\n".join(lines), title=title, border_style=style, padding=(0, 2)))
    return text
