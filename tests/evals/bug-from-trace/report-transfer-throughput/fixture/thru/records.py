"""The input boundary: turn a runs.csv into samples the metrics can trust.

This is the only place that sees raw CSV text. A row that does not describe a
completed run - no duration, or a duration of zero - is dropped here and its job
name is returned in :attr:`LoadResult.skipped`, so the report can say how many
rows it ignored. Everything downstream may assume ``seconds > 0``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = ("job", "bytes_out", "seconds")


@dataclass(frozen=True)
class Sample:
    job: str
    bytes_out: int
    seconds: float


@dataclass(frozen=True)
class LoadResult:
    samples: list[Sample]
    skipped: list[str]


def parse_row(row: dict[str, str]) -> Sample:
    return Sample(
        job=row["job"].strip(),
        bytes_out=int(row["bytes_out"] or 0),
        seconds=float(row["seconds"] or 0),
    )


def load_samples(path: Path) -> LoadResult:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"runs file is missing column(s): {', '.join(missing)}")
        rows = [parse_row(row) for row in reader]
    return LoadResult(samples=rows, skipped=[])
