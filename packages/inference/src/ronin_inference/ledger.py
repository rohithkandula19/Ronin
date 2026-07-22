"""Append-only usage ledger. Idempotent on request id — no double counting."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class UsageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    user_id: str
    org_id: str = ""
    provider: str = ""
    adapter: str = ""
    industry: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    est_cost: float | None = None  # None = unknown, never coerced to 0
    at: str = ""  # ISO, caller-supplied for determinism
    category: str = "inference"


class UsageLedger:
    """Local JSON-backed append-only ledger. Swap the backend behind this API."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._records: list[UsageRecord] = []
        self._seen: set[str] = set()
        if self._path.exists():
            for row in json.loads(self._path.read_text(encoding="utf-8")):
                rec = UsageRecord.model_validate(row)
                self._records.append(rec)
                self._seen.add(rec.request_id)

    def record(self, rec: UsageRecord) -> bool:
        """Append once per request_id. Returns False if already recorded (retry)."""
        if rec.request_id in self._seen:
            return False  # idempotent: a retry of the same request bills once
        self._records.append(rec)
        self._seen.add(rec.request_id)
        self._persist()
        return True

    def records(self) -> list[UsageRecord]:
        return list(self._records)

    def spent(self, *, user_id: str | None = None, org_id: str | None = None,
              provider: str | None = None, since: str | None = None) -> float:
        """Sum of KNOWN costs (None costs excluded, and flagged via unknown_cost_count)."""
        total = 0.0
        for r in self._filter(user_id, org_id, provider, since):
            if r.est_cost is not None:
                total += r.est_cost
        return total

    def tokens(self, *, user_id: str | None = None, org_id: str | None = None,
               provider: str | None = None, since: str | None = None) -> int:
        return sum(r.input_tokens + r.output_tokens
                   for r in self._filter(user_id, org_id, provider, since))

    def unknown_cost_count(self, **kw) -> int:
        return sum(1 for r in self._filter(kw.get("user_id"), kw.get("org_id"),
                                           kw.get("provider"), kw.get("since"))
                   if r.est_cost is None)

    def _filter(self, user_id, org_id, provider, since):
        for r in self._records:
            if user_id is not None and r.user_id != user_id:
                continue
            if org_id is not None and r.org_id != org_id:
                continue
            if provider is not None and r.provider != provider:
                continue
            if since is not None and r.at < since:
                continue
            yield r

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([r.model_dump(mode="json") for r in self._records], indent=2),
            encoding="utf-8",
        )
