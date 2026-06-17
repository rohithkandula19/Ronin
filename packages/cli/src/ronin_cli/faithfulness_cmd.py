"""``ronin faithfulness check`` -- score an answer against its sources.

A standalone CLI surface over the grounding harness: give it an answer (an
argument or piped stdin) and the files the agent claims to have used, and it
reports a calibrated 0..1 faithfulness score, the ungrounded claims, and any
hallucinated code symbols. Offline and deterministic; no provider, no keys.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ronin_hardening import FaithfulnessReport, Source

from .faithfulness_hook import MODE_WARN, FaithfulnessOutcome, evaluate_answer


@dataclass
class LoadedSources:
    sources: list[Source]
    missing: list[str]


def load_sources(paths: list[Path]) -> LoadedSources:
    """Read each path as a source. Paths that don't exist or can't be read are
    collected in ``missing`` so the caller can report them without crashing."""
    sources: list[Source] = []
    missing: list[str] = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            missing.append(str(p))
            continue
        if text.strip():
            sources.append(Source(origin=str(p), text=text))
    return LoadedSources(sources=sources, missing=missing)


def check(answer: str, source_paths: list[Path], *, mode: str = MODE_WARN) -> tuple[FaithfulnessOutcome, list[str]]:
    """Run the harness on ``answer`` against the contents of ``source_paths``.

    Returns the outcome plus the list of paths that could not be read.
    """
    loaded = load_sources(source_paths)
    outcome = evaluate_answer(answer, loaded.sources, mode=mode)
    return outcome, loaded.missing


def report_to_dict(report: FaithfulnessReport) -> dict:
    """A JSON-serializable view of the report, for ``--json``."""
    return {
        "score": report.score,
        "abstain": report.abstain,
        "reason": report.reason,
        "n_sources": report.n_sources,
        "grounded_fraction": round(report.grounded_fraction, 3),
        "hallucinated_symbols": list(report.hallucinated_symbols),
        "claims": [
            {
                "claim": c.claim,
                "grounded": c.grounded,
                "support": c.support,
                "method": c.method,
                "ungrounded_symbols": list(c.ungrounded_symbols),
            }
            for c in report.claims
        ],
    }


def to_json(outcome: FaithfulnessOutcome, *, missing: list[str]) -> str:
    payload = report_to_dict(outcome.report)
    payload["ungrounded"] = outcome.ungrounded
    payload["missing_sources"] = missing
    return json.dumps(payload, indent=2)


def exit_code(outcome: FaithfulnessOutcome) -> int:
    """0 = grounded, 1 = ungrounded, 2 = abstain. Lets a script branch on the
    verdict (e.g. block CI on an ungrounded model answer)."""
    if outcome.report.abstain:
        return 2
    return 1 if outcome.ungrounded else 0
