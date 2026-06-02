"""Statistical anomaly detection for the briefing.

PRODUCTION_LESSONS.md (future-work section #5) calls this out: a briefing that
just lists numbers gets boring fast; one that flags what's *unusual* doesn't.

We compare this week's metrics to a trailing window of past snapshots
(``BriefingSnapshot`` rows from ``briefing_history``). If any metric is
``z_threshold`` standard deviations away from the trailing mean, we surface it
as an Anomaly. Cheap, no LLM, deterministic.

Wire-in: ``main.py`` calls ``detect_anomalies(history, current_snapshot)``
after computing the current snapshot. If any anomalies fire, an extra section
is appended to the briefing Markdown.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .briefing_history import BriefingSnapshot


# (snapshot-attribute-name, display-name, format-fn)
_TRACKED_METRICS: list[tuple[str, str, callable]] = [
    ("mrr_cents",            "MRR",              lambda v: f"${v / 100:,.0f}"),
    ("active_subs",          "active subs",      lambda v: str(int(v))),
    ("new_subs_7d",          "new subs / week",  lambda v: str(int(v))),
    ("churned_subs_7d",      "churn / week",     lambda v: str(int(v))),
    ("failed_charges_7d",    "failed charges",   lambda v: str(int(v))),
    ("urgent_open_issues",   "urgent issues",    lambda v: str(int(v))),
]


@dataclass
class Anomaly:
    metric: str            # snapshot attribute name (e.g. "mrr_cents")
    label: str             # human-readable ("MRR")
    current: float
    mean: float
    stdev: float
    z_score: float
    direction: str         # "up" or "down"
    formatted_current: str = ""
    formatted_mean: str = ""


def _stats(values: list[float]) -> tuple[float, float]:
    """Mean + sample standard deviation. Returns (0, 0) for empty/1-elem inputs."""
    n = len(values)
    if n < 2:
        return (values[0] if values else 0.0, 0.0)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(variance)


def detect_anomalies(
    history: list[BriefingSnapshot],
    current: BriefingSnapshot,
    *,
    z_threshold: float = 2.0,
    min_history: int = 4,
) -> list[Anomaly]:
    """Return any metrics that are >``z_threshold`` σ from the trailing mean.

    ``history`` should be the prior snapshots (oldest → newest). ``current`` is
    the just-computed snapshot — not included in the baseline. We need at least
    ``min_history`` prior snapshots to compute a meaningful stdev; otherwise we
    return an empty list (no spurious anomalies on first runs).
    """
    if len(history) < min_history:
        return []

    anomalies: list[Anomaly] = []
    for attr, label, fmt in _TRACKED_METRICS:
        baseline = [float(getattr(snap, attr)) for snap in history]
        mean, stdev = _stats(baseline)
        if stdev == 0:
            continue  # no variance — can't meaningfully flag
        current_value = float(getattr(current, attr))
        z = (current_value - mean) / stdev
        if abs(z) < z_threshold:
            continue
        anomalies.append(
            Anomaly(
                metric=attr,
                label=label,
                current=current_value,
                mean=mean,
                stdev=stdev,
                z_score=round(z, 2),
                direction="up" if z > 0 else "down",
                formatted_current=fmt(current_value),
                formatted_mean=fmt(mean),
            )
        )
    return anomalies


def render_anomalies_section(anomalies: list[Anomaly]) -> str:
    """Render the anomalies section, ready to splice into the briefing Markdown.

    Returns an empty string when there are no anomalies (so the caller can
    unconditionally concatenate).
    """
    if not anomalies:
        return ""

    lines: list[str] = ["## 📊 Anomalies (vs trailing weeks)"]
    for a in anomalies:
        arrow = "↑" if a.direction == "up" else "↓"
        # Direction-aware framing
        verb = "above" if a.direction == "up" else "below"
        lines.append(
            f"- {arrow} **{a.label}** is {a.formatted_current} "
            f"({verb} mean {a.formatted_mean}, z={a.z_score})"
        )
    return "\n".join(lines)
