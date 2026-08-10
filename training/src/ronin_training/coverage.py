"""Check the built corpus against the per-family coverage floors in
`config/scenario_weights.yaml`.

The point is to stop the safety families (destructive floor, gated writes, injection
resistance, honest approvals) from silently thinning out as the corpus is edited. The
builder calls `coverage_gaps()` and prints the result in every report, so a family
that drops below its declared `min_examples` shows up as a GAP instead of going
unnoticed. A family present in the corpus but absent from the weights file is also
surfaced (it should be given a floor, even a small one).
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]           # training/
_WEIGHTS = _ROOT / "config" / "scenario_weights.yaml"


@cache
def load_weights(path: str | None = None) -> dict:
    p = Path(path) if path else _WEIGHTS
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data.get("families", {})


def coverage_gaps(family_counts: dict[str, int], weights: dict | None = None) -> list[str]:
    """Human-readable coverage problems, [] if every floor is met.

    - a family below its `min_examples` floor → GAP
    - a family in the corpus with no declared floor → UNWEIGHTED (should be added)
    """
    fams = weights if weights is not None else load_weights()
    problems: list[str] = []
    for name, spec in sorted(fams.items()):
        floor = int(spec.get("min_examples", 0))
        have = family_counts.get(name, 0)
        if have < floor:
            tier = spec.get("tier", "?")
            problems.append(
                f"GAP [{tier}] {name}: {have}/{floor} examples (below floor)"
            )
    for name in sorted(family_counts):
        if name not in fams:
            problems.append(f"UNWEIGHTED {name}: present in corpus but no coverage floor declared")
    return problems


def coverage_table(family_counts: dict[str, int], weights: dict | None = None) -> list[tuple]:
    """(tier, family, have, floor, met) rows for reporting, sorted by tier then name."""
    fams = weights if weights is not None else load_weights()
    rows = []
    for name, spec in fams.items():
        floor = int(spec.get("min_examples", 0))
        have = family_counts.get(name, 0)
        rows.append((spec.get("tier", "?"), name, have, floor, have >= floor))
    return sorted(rows, key=lambda r: (r[0], r[1]))
