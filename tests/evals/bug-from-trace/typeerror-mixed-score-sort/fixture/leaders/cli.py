"""Print the leaderboard."""

from __future__ import annotations

from leaders.rank import top

ROWS = [
    {"name": "ana", "score": 7},
    {"name": "bo", "score": "12"},
    {"name": "cy", "score": 3},
]


def main() -> None:
    for name in top(ROWS, 2):
        print(name)
