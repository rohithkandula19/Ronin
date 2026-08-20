"""Token and cost accounting per request and per session, persisted to sqlite.

Persisted because the number people actually want is "what did this week cost",
and an in-memory counter answers "what did this process cost" — which nobody asks.
``sqlite3`` is in the standard library, gives real aggregation, and survives a
crash mid-session; a JSONL file would need us to write the aggregation ourselves.

Two rules that keep the numbers honest:

* **Unpriced is not free.** A model with no price in config records
  ``cost_usd = 0`` *and* sets ``priced = 0`` on the row, so a total can say "plus
  N unpriced requests" instead of quietly implying a $0 session.
* **Unreported is not zero.** A provider that sends no usage produces a row with
  zero tokens and ``reported = 0``. The distinction between "used no tokens" and
  "did not say" is the difference between a working budget and a broken one.

Time is injected (``clock``) rather than read from the wall, so ledger tests are
deterministic and a replayed session gets its original timestamps.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .router import ModelSpec, Role
from .types import Usage

#: Per-million-token → per-token. Prices are quoted per million everywhere.
PER_MILLION = 1_000_000.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    request_id      TEXT    NOT NULL,
    ts              REAL    NOT NULL,
    role            TEXT    NOT NULL,
    provider        TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cache_read      INTEGER NOT NULL,
    cache_write     INTEGER NOT NULL,
    cost_usd        REAL    NOT NULL,
    -- 0 when the model has no price in config: the cost column is a floor, not a total.
    priced          INTEGER NOT NULL,
    -- 0 when the provider reported no usage at all: zero tokens means "unknown".
    reported        INTEGER NOT NULL,
    prefix_fp       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS requests_session ON requests (session_id);
CREATE UNIQUE INDEX IF NOT EXISTS requests_request ON requests (session_id, request_id);
"""


def price(usage: Usage, spec: ModelSpec) -> float:
    """Cost in USD for ``usage`` at ``spec``'s prices. Zero when unpriced.

    Cached reads are billed at their own (much lower) rate when config gives one
    and at the input rate when it does not — the conservative direction, since
    reporting a cache discount that the provider did not give understates the bill.
    """
    if usage.cost_usd:
        # The provider told us. Prefer its number over our price table.
        return usage.cost_usd
    if not spec.priced:
        return 0.0
    cache_rate = spec.price_cache_read or spec.price_in
    return (
        usage.input_tokens * spec.price_in
        + usage.output_tokens * spec.price_out
        + usage.cache_read_tokens * cache_rate
        + usage.cache_write_tokens * spec.price_in
    ) / PER_MILLION


@dataclass(frozen=True, slots=True)
class Totals:
    """Aggregated usage over some set of requests."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    #: Requests whose model had no price. Their tokens are counted; their cost is not.
    unpriced_requests: int = 0
    #: Requests where the provider reported no usage. Their tokens are unknown.
    unreported_requests: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_read_tokens + self.cache_write_tokens + self.input_tokens
        return self.cache_read_tokens / total if total else 0.0

    def summary(self) -> str:
        """One honest line, caveats included rather than rounded away."""
        parts = [
            f"{self.requests} request(s)",
            f"{self.input_tokens:,} in / {self.output_tokens:,} out",
            f"cache {self.cache_hit_rate:.0%}",
            f"${self.cost_usd:.4f}",
        ]
        if self.unpriced_requests:
            parts.append(f"+{self.unpriced_requests} unpriced")
        if self.unreported_requests:
            parts.append(f"{self.unreported_requests} with no usage reported")
        return " · ".join(parts)


class Ledger:
    """The persisted record of every model request.

    Opened per use rather than held open: an agent session is long-lived and a
    held sqlite connection turns a crash into a lock file somebody has to delete.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], float] | None = None) -> None:
        self._path = Path(path)
        # Injected so tests are deterministic; the default reads the wall clock.
        self._clock = clock if clock is not None else _wall_clock
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(
        self,
        *,
        session_id: str,
        request_id: str,
        role: Role,
        spec: ModelSpec,
        usage: Usage,
        reported: bool = True,
        prefix_fingerprint: str = "",
    ) -> float:
        """Persist one request and return the cost charged for it.

        Idempotent on ``(session_id, request_id)``: a retried write updates the row
        rather than double-counting, because the natural retry path (the loop
        re-recording after a reconnect) would otherwise inflate every bill.
        """
        cost = price(usage, spec)
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO requests (
                    session_id, request_id, ts, role, provider, model,
                    input_tokens, output_tokens, cache_read, cache_write,
                    cost_usd, priced, reported, prefix_fp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (session_id, request_id) DO UPDATE SET
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    cache_read = excluded.cache_read,
                    cache_write = excluded.cache_write,
                    cost_usd = excluded.cost_usd,
                    priced = excluded.priced,
                    reported = excluded.reported
                """,
                (
                    session_id,
                    request_id,
                    self._clock(),
                    role.value,
                    spec.provider,
                    spec.model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read_tokens,
                    usage.cache_write_tokens,
                    cost,
                    1 if spec.priced or usage.cost_usd else 0,
                    1 if reported else 0,
                    prefix_fingerprint,
                ),
            )
            conn.commit()
        return cost

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def session_totals(self, session_id: str) -> Totals:
        """Everything spent in one session."""
        return self._totals("WHERE session_id = ?", (session_id,))

    def totals(self) -> Totals:
        """Everything spent, ever."""
        return self._totals("", ())

    def role_totals(self, session_id: str) -> dict[Role, Totals]:
        """Per-role breakdown. This is where "main did the grep triage" shows up."""
        out: dict[Role, Totals] = {}
        for role in Role:
            totals = self._totals("WHERE session_id = ? AND role = ?", (session_id, role.value))
            if totals.requests:
                out[role] = totals
        return out

    def _totals(self, where: str, params: tuple[object, ...]) -> Totals:
        query = f"""
            SELECT
                COUNT(*)                        AS requests,
                COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read), 0)    AS cache_read,
                COALESCE(SUM(cache_write), 0)   AS cache_write,
                COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
                COALESCE(SUM(1 - priced), 0)    AS unpriced,
                COALESCE(SUM(1 - reported), 0)  AS unreported
            FROM requests {where}
        """
        with closing(self._connect()) as conn:
            row = conn.execute(query, params).fetchone()
        return Totals(
            requests=int(row["requests"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cache_read_tokens=int(row["cache_read"]),
            cache_write_tokens=int(row["cache_write"]),
            cost_usd=float(row["cost_usd"]),
            unpriced_requests=int(row["unpriced"]),
            unreported_requests=int(row["unreported"]),
        )

    def sessions(self) -> tuple[str, ...]:
        """Every session id in the ledger, most recent first."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT session_id, MAX(ts) AS last FROM requests "
                "GROUP BY session_id ORDER BY last DESC"
            ).fetchall()
        return tuple(str(row["session_id"]) for row in rows)

    def prefix_fingerprints(self, session_id: str) -> dict[str, int]:
        """How often each stable prefix was sent in a session.

        A session with many distinct fingerprints has a prefix that is not stable,
        which is the diagnosis for "caching is enabled and the bill did not move".
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT prefix_fp, COUNT(*) AS n FROM requests "
                "WHERE session_id = ? AND prefix_fp != '' GROUP BY prefix_fp",
                (session_id,),
            ).fetchall()
        return {str(row["prefix_fp"]): int(row["n"]) for row in rows}


def _wall_clock() -> float:
    import time

    return time.time()


def sum_usage(items: Iterable[Usage]) -> Usage:
    """Fold usages together. Handy for a turn that made several requests."""
    total = Usage()
    for item in items:
        total = total + item
    return total
