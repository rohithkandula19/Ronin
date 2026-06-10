"""Persist Cost Router savings so `ronin costs` shows *lifetime* savings.

The CostLedger only lives for one session. This appends one line per routed turn
to ``.ronin/savings.jsonl`` — what the turn actually cost vs what the strong blade
would have — so savings accumulate across every session and survive restarts.
Append + summarize are pure-ish (filesystem only) and unit-tested.
"""
from __future__ import annotations

import json
from pathlib import Path

_SAVINGS_FILE = Path(".ronin") / "savings.jsonl"


def savings_path(root: Path | str) -> Path:
    return Path(root) / _SAVINGS_FILE


def append_turn(root: Path | str, actual: float, baseline: float, free: bool) -> None:
    """Record one routed turn's cost vs baseline. Best-effort, silent on failure."""
    path = savings_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"actual": round(actual, 6),
                                 "baseline": round(baseline, 6),
                                 "free": bool(free)}) + "\n")
    except OSError:
        pass


def load_summary(root: Path | str) -> dict:
    """Aggregate lifetime savings: spent, baseline, saved, turns, free_turns."""
    path = savings_path(root)
    spent = baseline = 0.0
    turns = free_turns = 0
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                spent += float(rec.get("actual", 0.0))
                baseline += float(rec.get("baseline", 0.0))
                turns += 1
                if rec.get("free"):
                    free_turns += 1
        except (OSError, ValueError, TypeError):
            pass
    return {"spent": spent, "baseline": baseline, "saved": max(0.0, baseline - spent),
            "turns": turns, "free_turns": free_turns}
