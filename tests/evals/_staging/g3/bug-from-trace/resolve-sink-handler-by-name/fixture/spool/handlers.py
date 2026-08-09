"""The sink handlers spool knows how to write a batch to."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Handler:
    """A named sink, described by where it writes and how it batches."""

    name: str
    target: str
    batch_size: int

    def describe(self) -> str:
        return f"{self.name} -> {self.target} (batches of {self.batch_size})"

    def plan(self, rows: int) -> int:
        """How many writes this handler needs for ``rows`` rows."""
        if rows <= 0:
            return 0
        return -(-rows // self.batch_size)


POSTGRES = Handler(name="postgres", target="warehouse.public.events", batch_size=500)
S3 = Handler(name="s3", target="s3://spool-archive/events/", batch_size=2000)
SMTP = Handler(name="smtp", target="digest@example.invalid", batch_size=50)
