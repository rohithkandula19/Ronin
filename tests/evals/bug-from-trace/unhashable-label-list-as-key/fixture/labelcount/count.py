"""Count label occurrences across issues.

Each issue carries a list of labels, and every label in that list counts
once for that issue.
"""

from __future__ import annotations

from typing import Any


def count_labels(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        key = issue["labels"]
        counts[key] = counts.get(key, 0) + 1
    return counts
