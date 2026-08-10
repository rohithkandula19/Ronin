"""Print the label histogram."""

from __future__ import annotations

from labelcount.count import count_labels

ISSUES = [
    {"id": 1, "labels": ["bug", "p1"]},
    {"id": 2, "labels": ["bug"]},
    {"id": 3, "labels": []},
]


def main() -> None:
    counts = count_labels(ISSUES)
    for label in sorted(counts):
        print("%s %d" % (label, counts[label]))
