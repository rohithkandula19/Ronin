"""Self-tuning Router — ronin learns which blade wins on *your* repo.

The Cost Router picks a provider per turn from a keyword heuristic. This adds the
missing feedback loop: after each routed turn we record whether it *worked*
(the agent finished cleanly) or *didn't* (errored / got blocked). Over time, if
the cheap/free blade keeps failing a certain kind of task in this repo, the
router stops sending that kind there and escalates to the strong blade on its
own — and surfaces the shift so you can see it learning.

Stats live in ``.ronin/router_stats.json`` (per-repo, committable if you want to
share the learned profile with your team). Pure store + decision logic, fully
unit-tested; the session loop just feeds it outcomes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_STATS_FILE = Path(".ronin") / "router_stats.json"
# Don't escalate on noise: need this many samples before the success rate counts.
MIN_SAMPLES = 4
# Below this success rate (with enough samples) the cheap blade is "unreliable"
# for that tier and the router escalates it.
ESCALATE_BELOW = 0.6


@dataclass
class RouterStats:
    """Per-(tier, provider) success tallies: ``data[tier][provider] = [ok, total]``."""
    data: dict[str, dict[str, list[int]]] = field(default_factory=dict)

    def record(self, tier: str, provider: str, success: bool) -> None:
        prov = self.data.setdefault(tier, {}).setdefault(provider, [0, 0])
        prov[0] += 1 if success else 0
        prov[1] += 1

    def rate(self, tier: str, provider: str) -> float | None:
        """Success rate for (tier, provider), or None if too few samples."""
        prov = self.data.get(tier, {}).get(provider)
        if not prov or prov[1] < MIN_SAMPLES:
            return None
        return prov[0] / prov[1]

    def should_escalate(self, tier: str, provider: str) -> bool:
        """True when ``provider`` has proven unreliable for ``tier`` here — enough
        samples AND a success rate under the threshold."""
        r = self.rate(tier, provider)
        return r is not None and r < ESCALATE_BELOW

    def summary(self, tier: str, provider: str) -> str:
        prov = self.data.get(tier, {}).get(provider, [0, 0])
        return f"{prov[0]}/{prov[1]}"


def stats_path(root: Path | str) -> Path:
    return Path(root) / _STATS_FILE


def rows(stats: "RouterStats") -> list[dict]:
    """Flatten the stats into display rows (one per tier+provider), sorted by
    tier then provider. Each row: tier, provider, ok, total, rate, status."""
    out: list[dict] = []
    for tier in sorted(stats.data):
        for provider in sorted(stats.data[tier]):
            ok, total = stats.data[tier][provider]
            rate = stats.rate(tier, provider)
            if total < MIN_SAMPLES:
                status = "learning"
            elif stats.should_escalate(tier, provider):
                status = "escalate"   # proven unreliable here
            else:
                status = "reliable"
            out.append({"tier": tier, "provider": provider, "ok": ok, "total": total,
                        "rate": rate, "status": status})
    return out


def load_stats(root: Path | str) -> RouterStats:
    path = stats_path(root)
    if not path.is_file():
        return RouterStats()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # normalize to list[int] pairs (json gives lists already)
        data = {t: {p: [int(v[0]), int(v[1])] for p, v in provs.items()}
                for t, provs in raw.items()}
        return RouterStats(data=data)
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return RouterStats()


def save_stats(root: Path | str, stats: RouterStats) -> None:
    path = stats_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats.data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
