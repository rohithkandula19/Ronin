"""Wiring: read -> transform -> filter -> validate -> write."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Sequence

from .readers.base import Reader
from .transforms.base import Filter, Record, Transform
from .validate import RowError, Validator


@dataclass(frozen=True)
class PipelineResult:
    written: int
    rejected: int
    dropped: int
    errors: tuple[RowError, ...] = ()

    @property
    def rejected_ratio(self) -> float:
        seen = self.written + self.rejected
        if seen == 0:
            return 0.0
        return self.rejected / seen

    def render(self) -> str:
        return (
            f"imported {self.written} rows, rejected {self.rejected} rows, "
            f"dropped {self.dropped} duplicate rows"
        )


@dataclass
class Pipeline:
    reader: Reader
    validator: Validator
    writer: object
    transforms: Sequence[Transform] = dataclass_field(default_factory=tuple)
    filters: Sequence[Filter] = dataclass_field(default_factory=tuple)

    def run(self) -> PipelineResult:
        written = 0
        rejected = 0
        dropped = 0
        errors: list[RowError] = []
        for row_number, row in self.reader.read():
            record: Record = dict(row)
            for transform in self.transforms:
                record = transform.apply(record)
            if not all(f.keep(record) for f in self.filters):
                dropped += 1
                continue
            row_errors = self.validator.check(row_number, record)
            if row_errors:
                rejected += 1
                errors.extend(row_errors)
                continue
            self.writer.write(record)
            written += 1
        return PipelineResult(
            written=written, rejected=rejected, dropped=dropped, errors=tuple(errors)
        )
