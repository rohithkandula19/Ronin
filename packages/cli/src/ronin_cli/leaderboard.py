"""Dojo leaderboard — which blade actually wins on *your* tasks, over time.

Every `ronin dojo` pits providers against each other on one task. On its own
that's a one-off. The leaderboard tallies wins across all your dojos in
``.ronin/dojo_leaderboard.json`` so a picture builds up: the model that keeps
winning *your* kind of work is the one you should point `route_strong` at. Pure
store + standings + recommendation; the dojo command just feeds it results.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_FILE = Path(".ronin") / "dojo_leaderboard.json"
# Don't recommend off noise — need this many contests before a winner is trusted.
MIN_CONTESTS = 3


@dataclass
class Leaderboard:
    """Per-provider ``[wins, contests]`` tallies."""
    data: dict[str, list[int]] = field(default_factory=dict)

    def record(self, winner: str | None, contenders: list[str]) -> None:
        """Tally one dojo: every contender gets a contest, the winner a win."""
        for c in set(contenders):
            row = self.data.setdefault(c, [0, 0])
            row[1] += 1
        if winner is not None:
            self.data.setdefault(winner, [0, 0])[0] += 1

    def win_rate(self, provider: str) -> float | None:
        row = self.data.get(provider)
        if not row or row[1] == 0:
            return None
        return row[0] / row[1]

    def standings(self) -> list[dict]:
        """Providers ranked by win rate (then contests), one row each."""
        rows = []
        for prov, (wins, contests) in self.data.items():
            rows.append({"provider": prov, "wins": wins, "contests": contests,
                         "rate": wins / contests if contests else 0.0})
        rows.sort(key=lambda r: (r["rate"], r["contests"]), reverse=True)
        return rows

    def recommend_strong(self) -> str | None:
        """The provider to point route_strong at — the clear winner with enough
        contests — or None if there isn't a confident leader yet."""
        ranked = [r for r in self.standings() if r["contests"] >= MIN_CONTESTS]
        if not ranked:
            return None
        top = ranked[0]
        return top["provider"] if top["rate"] > 0.5 else None


def leaderboard_path(root: Path | str) -> Path:
    return Path(root) / _FILE


def load_leaderboard(root: Path | str) -> Leaderboard:
    path = leaderboard_path(root)
    if not path.is_file():
        return Leaderboard()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = {p: [int(v[0]), int(v[1])] for p, v in raw.items()}
        return Leaderboard(data=data)
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return Leaderboard()


def save_leaderboard(root: Path | str, lb: Leaderboard) -> None:
    path = leaderboard_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(lb.data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
