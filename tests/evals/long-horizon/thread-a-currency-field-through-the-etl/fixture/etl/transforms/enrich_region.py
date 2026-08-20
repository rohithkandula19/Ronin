"""Expand the legacy region codes some shops still emit."""

from __future__ import annotations

from .base import Record, Transform

LEGACY_REGIONS = {
    "eu": "emea",
    "eu-west": "emea",
    "ap": "apac",
    "na": "amer",
    "us": "amer",
}


class EnrichRegion(Transform):
    name = "enrich-region"

    def apply(self, record: Record) -> Record:
        region = record.get("region", "")
        if region and region in LEGACY_REGIONS:
            out = dict(record)
            out["region"] = LEGACY_REGIONS[region]
            return out
        return record
