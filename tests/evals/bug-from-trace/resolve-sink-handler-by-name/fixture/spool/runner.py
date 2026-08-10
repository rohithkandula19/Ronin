"""Turns a job file into a human-readable dispatch plan."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from spool.registry import find_handler


def load_jobs(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return list(document["jobs"])


def plan_job(job: dict[str, Any]) -> str:
    handler = find_handler(job["sink"])
    writes = handler.plan(job["rows"])
    return f"{job['id']}: {handler.describe()} => {writes} write(s)"


def plan_all(jobs: Sequence[dict[str, Any]]) -> list[str]:
    return [plan_job(job) for job in jobs]
