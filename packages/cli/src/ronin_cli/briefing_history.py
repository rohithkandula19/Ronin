"""Briefing history — auto-save each run + compute week-over-week deltas.

Storage layout:
    .csk/briefings/<YYYY-MM-DD>.json   one file per briefing run, indexed by date

The on-disk shape is a JSON-serializable view of the metrics we care about for
trending (not the full ``BriefingData`` — issue lists rotate too quickly to be
useful as a time series). New fields can be added safely; missing fields default
to 0 when computing deltas.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .briefing import BriefingData


HISTORY_DIR = Path(".csk") / "briefings"


@dataclass
class BriefingSnapshot:
    """Minimum trend-worthy slice of a briefing, persisted per run."""

    date: str  # YYYY-MM-DD UTC
    mrr_cents: int = 0
    arr_cents: int = 0
    active_subs: int = 0
    new_subs_7d: int = 0
    churned_subs_7d: int = 0
    past_due_subs: int = 0
    succeeded_charges_7d: int = 0
    failed_charges_7d: int = 0
    refunded_charges_7d: int = 0
    urgent_open_issues: int = 0
    high_open_issues: int = 0
    in_progress_issues: int = 0

    @classmethod
    def from_briefing(cls, data: BriefingData, *, date: str | None = None) -> "BriefingSnapshot":
        return cls(
            date=date or data.today_iso or datetime.now(timezone.utc).date().isoformat(),
            mrr_cents=data.mrr_cents,
            arr_cents=data.arr_cents,
            active_subs=len(data.active_subs),
            new_subs_7d=len(data.new_subs_7d),
            churned_subs_7d=len(data.churned_subs_7d),
            past_due_subs=len(data.past_due_subs),
            succeeded_charges_7d=len(data.succeeded_charges_7d),
            failed_charges_7d=len(data.failed_charges_7d),
            refunded_charges_7d=len(data.refunded_charges_7d),
            urgent_open_issues=len(data.urgent_open_issues),
            high_open_issues=len(data.high_open_issues),
            in_progress_issues=len(data.in_progress_issues),
        )


@dataclass
class BriefingDelta:
    """Field-by-field deltas between two snapshots."""

    mrr_cents: int = 0
    active_subs: int = 0
    new_subs_7d: int = 0
    churned_subs_7d: int = 0
    failed_charges_7d: int = 0
    urgent_open_issues: int = 0
    high_open_issues: int = 0

    @classmethod
    def compute(cls, current: BriefingSnapshot, prior: BriefingSnapshot) -> "BriefingDelta":
        return cls(
            mrr_cents=current.mrr_cents - prior.mrr_cents,
            active_subs=current.active_subs - prior.active_subs,
            new_subs_7d=current.new_subs_7d - prior.new_subs_7d,
            churned_subs_7d=current.churned_subs_7d - prior.churned_subs_7d,
            failed_charges_7d=current.failed_charges_7d - prior.failed_charges_7d,
            urgent_open_issues=current.urgent_open_issues - prior.urgent_open_issues,
            high_open_issues=current.high_open_issues - prior.high_open_issues,
        )


def history_dir(root: Path | None = None) -> Path:
    return (root or Path.cwd()) / HISTORY_DIR


def save_snapshot(snapshot: BriefingSnapshot, *, root: Path | None = None) -> Path:
    """Persist a snapshot. If a file for the same date exists, it's overwritten."""
    target_dir = history_dir(root)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{snapshot.date}.json"
    path.write_text(json.dumps(asdict(snapshot), indent=2) + "\n", encoding="utf-8")
    return path


def load_snapshots(root: Path | None = None) -> list[BriefingSnapshot]:
    """Return all saved snapshots, oldest first."""
    target_dir = history_dir(root)
    if not target_dir.exists():
        return []
    snapshots: list[BriefingSnapshot] = []
    for path in sorted(target_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            snapshots.append(BriefingSnapshot(**{k: payload.get(k, 0) for k in BriefingSnapshot.__dataclass_fields__ if k != "date"}, date=payload.get("date", path.stem)))
        except (json.JSONDecodeError, TypeError):
            continue
    return snapshots


def most_recent_prior(current_date: str, root: Path | None = None) -> BriefingSnapshot | None:
    """The latest snapshot dated *before* ``current_date``. Used for delta-vs-last."""
    snapshots = load_snapshots(root)
    prior = [s for s in snapshots if s.date < current_date]
    return prior[-1] if prior else None


def format_delta_line(delta: BriefingDelta, prior_date: str) -> str:
    """Render the delta as a one-line summary appended to the revenue section."""
    bits: list[str] = []
    if delta.mrr_cents:
        sign = "+" if delta.mrr_cents > 0 else ""
        bits.append(f"MRR {sign}${delta.mrr_cents / 100:.0f}")
    if delta.new_subs_7d:
        sign = "+" if delta.new_subs_7d > 0 else ""
        bits.append(f"new subs {sign}{delta.new_subs_7d}")
    if delta.churned_subs_7d:
        sign = "+" if delta.churned_subs_7d > 0 else ""
        bits.append(f"churn {sign}{delta.churned_subs_7d}")
    if delta.failed_charges_7d:
        sign = "+" if delta.failed_charges_7d > 0 else ""
        bits.append(f"failed charges {sign}{delta.failed_charges_7d}")
    if delta.urgent_open_issues:
        sign = "+" if delta.urgent_open_issues > 0 else ""
        bits.append(f"urgent {sign}{delta.urgent_open_issues}")
    if not bits:
        return f"_no change vs {prior_date}_"
    return f"_vs {prior_date}: " + ", ".join(bits) + "_"
