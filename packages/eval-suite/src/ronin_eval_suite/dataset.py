from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .types import EvalCase


class GoldenDataset:
    """JSONL-backed collection of ``EvalCase``.

    Each line is one JSON object matching ``EvalCase``. ``id`` is required and unique.
    """

    def __init__(self, cases: list[EvalCase]):
        seen: set[str] = set()
        for c in cases:
            if c.id in seen:
                raise ValueError(f"duplicate case id: {c.id}")
            seen.add(c.id)
        self.cases = cases

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "GoldenDataset":
        cases: list[EvalCase] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(EvalCase.model_validate_json(line))
        return cls(cases)

    def to_jsonl(self, path: str | Path) -> None:
        Path(path).write_text(
            "\n".join(c.model_dump_json() for c in self.cases) + "\n",
            encoding="utf-8",
        )

    def __iter__(self) -> Iterator[EvalCase]:
        return iter(self.cases)

    def __len__(self) -> int:
        return len(self.cases)
