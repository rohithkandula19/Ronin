"""Throughput arithmetic over already-validated samples."""

from __future__ import annotations

from collections.abc import Sequence

from thru.records import Sample

MEBIBYTE = 1024 * 1024


def rate_mb_s(sample: Sample) -> float:
    """Mebibytes per second for one completed run."""
    return sample.bytes_out / MEBIBYTE / sample.seconds


def report(samples: Sequence[Sample]) -> list[str]:
    lines = [f"{sample.job:<16}{rate_mb_s(sample):8.2f} MB/s" for sample in samples]
    total_bytes = sum(sample.bytes_out for sample in samples)
    total_seconds = sum(sample.seconds for sample in samples)
    lines.append(f"{'overall':<16}{total_bytes / MEBIBYTE / total_seconds:8.2f} MB/s")
    return lines
