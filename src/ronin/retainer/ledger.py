"""The effect ledger: what a Retainer has already done to the outside world.

``docs/RETAINER.md`` §6.6. Webhooks redeliver, and a resumed run replays the
steps that preceded the escalation. So "post the comment" is *reached* more than
once for one logical act as a matter of course rather than as a bug, and without
a ledger the second arrival is a second comment.

**This is not a cache, and that is the whole difference from
:mod:`ronin.persistence.index`.** The session index is derived from the
transcripts, so deleting it costs a rebuild and loses nothing; three of its
decisions follow from that, and all three invert here:

* A schema mismatch **refuses to open** instead of dropping and rebuilding.
  There is nothing to rebuild from — the record of what was said to GitHub only
  exists here — so a version bump is a migration somebody has to write, not a
  ``DROP TABLE``.
* Writes **raise** instead of degrading into a problem list. The index can miss a
  row and the session is still fine; if this file misses a row, the next
  redelivery posts a duplicate. A ledger that fails quietly is worse than no
  ledger, because the caller believes it has one.
* There is no ``rebuild()``.

**Claim, then complete — never check, then act.** The obvious API is
``if not ledger.seen(effect): post()``. It is wrong twice over. It races two
deliveries of the same webhook, and it cannot tell "posted" from "crashed
mid-post". So :meth:`EffectLedger.claim` inserts the row *before* the act, in one
atomic statement, and returns ``None`` if somebody already holds it;
:meth:`EffectLedger.complete` closes it afterwards.

A row that is claimed and never completed is therefore a genuine unknown: the
process died somewhere around the act, and nobody can say from here whether the
comment exists. Those rows stay claimed and :meth:`EffectLedger.pending` lists
them, because "I may or may not have already done this" is exactly the state a
human needs shown rather than resolved by a guess.

Depends on ``sqlite3`` from the standard library. No new dependency.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from ronin.retainer.model import Effect, EffectKind

#: Bumped when the schema below changes. A mismatch refuses to open — see the
#: module docstring. This file is the record, not a view of one.
LEDGER_SCHEMA_VERSION: Final = 1

LEDGER_FILENAME: Final = "effects.sqlite3"

#: Two adapters can deliver at once, so a writer may briefly wait on a writer.
#: WAL means readers never block, which is what keeps this small.
DEFAULT_TIMEOUT_SECONDS: Final = 5.0

#: The primary key is the effect's identity, so the database enforces
#: idempotency rather than the caller remembering to. ``done_at`` being NULL is
#: the crash state and is queried, hence the index.
SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS effects (
    retainer   TEXT NOT NULL,
    summons    TEXT NOT NULL,
    step       TEXT NOT NULL,
    digest     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    target     TEXT NOT NULL,
    claimed_at REAL NOT NULL,
    done_at    REAL,
    result     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (retainer, summons, step, digest)
);
CREATE INDEX IF NOT EXISTS effects_open ON effects (retainer, done_at);
"""


class LedgerError(RuntimeError):
    """The ledger could not be read or written. Never swallowed — see the module docstring."""


class EffectStatus(StrEnum):
    """What the ledger knows about one effect."""

    UNSEEN = "unseen"
    """No row. Safe to claim."""

    PENDING = "pending"
    """Claimed and not completed: it may or may not have happened. Needs a human."""

    DONE = "done"
    """Completed. Doing it again would duplicate it."""


@dataclass(frozen=True, slots=True)
class Claim:
    """Permission to perform one effect exactly once, held by this caller.

    Holding a claim is not the same as having acted. It means nobody else will
    act on this effect, and that a crash from here on is *visible* rather than
    indistinguishable from never having started.
    """

    effect: Effect
    claimed_at: float

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.effect.key


@dataclass(frozen=True, slots=True)
class Pending:
    """A claim nobody closed. The honest name for "we do not know"."""

    retainer: str
    summons: str
    step: str
    digest: str
    kind: EffectKind
    target: str
    claimed_at: float

    def describe(self) -> str:
        return (
            f"{self.kind.value} on {self.target} was claimed by {self.retainer} "
            f"for summons {self.summons} step {self.step} and never completed — "
            "it may or may not have happened"
        )


@dataclass(frozen=True, slots=True)
class EffectLedger:
    """The record of every outward act, keyed so each can happen only once."""

    path: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    clock: Callable[[], float] = time.time

    @classmethod
    def open(
        cls,
        directory: Path,
        *,
        filename: str = LEDGER_FILENAME,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> EffectLedger:
        """Create or adopt the ledger under ``directory``, schema ready to use.

        Raises :class:`LedgerError` on anything that would leave the caller
        without a working ledger — including a schema version this build does not
        understand, which is refused rather than discarded.
        """
        directory.mkdir(parents=True, exist_ok=True)
        ledger = cls(path=directory / filename, timeout=timeout, clock=clock)
        ledger._prepare()
        return ledger

    # ------------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.path, timeout=self.timeout)
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot open the effect ledger at {self.path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _prepare(self) -> None:
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("PRAGMA journal_mode=WAL")
                found = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if found and found != LEDGER_SCHEMA_VERSION:
                    raise LedgerError(
                        f"the effect ledger at {self.path} is schema v{found} and this "
                        f"build understands v{LEDGER_SCHEMA_VERSION}. It records what "
                        "was already said and done, so it is not safe to discard: "
                        "migrate it or move it aside deliberately."
                    )
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA user_version={LEDGER_SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot prepare the effect ledger at {self.path}: {exc}") from exc

    # ------------------------------------------------------------------ writing

    def claim(self, effect: Effect) -> Claim | None:
        """Take exclusive permission to perform ``effect``, or ``None`` if taken.

        One statement, so two simultaneous deliveries of the same webhook cannot
        both win: the primary key decides, not the order two callers happened to
        read in.
        """
        now = self.clock()
        retainer, summons, step, digest = effect.key
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "INSERT INTO effects "
                    "(retainer, summons, step, digest, kind, target, claimed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    (retainer, summons, step, digest, effect.kind.value, effect.target, now),
                )
                claimed = cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise LedgerError(
                f"cannot claim {effect.kind.value} on {effect.target}: {exc}"
            ) from exc
        return Claim(effect=effect, claimed_at=now) if claimed else None

    def complete(self, claim: Claim, *, result: str = "") -> None:
        """Close a claim once the act has actually happened.

        Completing a claim that is not open is a programming error rather than a
        race: the claim is the proof that this caller, and only this caller, was
        going to do it.
        """
        retainer, summons, step, digest = claim.key
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "UPDATE effects SET done_at = ?, result = ? "
                    "WHERE retainer = ? AND summons = ? AND step = ? AND digest = ? "
                    "AND done_at IS NULL",
                    (self.clock(), result, retainer, summons, step, digest),
                )
                closed = cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot complete {claim.effect.kind.value}: {exc}") from exc
        if not closed:
            raise LedgerError(
                f"no open claim for {claim.effect.kind.value} on {claim.effect.target} — "
                "it was completed already, or this claim was never held"
            )

    def abandon(self, claim: Claim) -> None:
        """Release a claim for an act that provably did not happen.

        Only correct where the failure is known to have occurred *before* the
        outward act — a request that was refused, a body that would not render.
        Where it is not known, leave the claim open and let
        :meth:`pending` surface it; guessing is how a duplicate gets posted.
        """
        retainer, summons, step, digest = claim.key
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "DELETE FROM effects WHERE retainer = ? AND summons = ? "
                    "AND step = ? AND digest = ? AND done_at IS NULL",
                    (retainer, summons, step, digest),
                )
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot abandon {claim.effect.kind.value}: {exc}") from exc

    # ------------------------------------------------------------------ reading

    def status(self, effect: Effect) -> EffectStatus:
        """What the ledger knows about ``effect``, without changing anything."""
        retainer, summons, step, digest = effect.key
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT done_at FROM effects WHERE retainer = ? AND summons = ? "
                    "AND step = ? AND digest = ?",
                    (retainer, summons, step, digest),
                ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot read the effect ledger at {self.path}: {exc}") from exc
        if row is None:
            return EffectStatus.UNSEEN
        return EffectStatus.DONE if row["done_at"] is not None else EffectStatus.PENDING

    def pending(self, retainer: str = "") -> tuple[Pending, ...]:
        """Every claim nobody closed, oldest first. Optionally for one Retainer."""
        sql = (
            "SELECT retainer, summons, step, digest, kind, target, claimed_at "
            "FROM effects WHERE done_at IS NULL"
        )
        parameters: tuple[str, ...] = ()
        if retainer:
            sql += " AND retainer = ?"
            parameters = (retainer,)
        sql += " ORDER BY claimed_at, step"
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, parameters).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot read the effect ledger at {self.path}: {exc}") from exc
        return tuple(
            Pending(
                retainer=str(row["retainer"]),
                summons=str(row["summons"]),
                step=str(row["step"]),
                digest=str(row["digest"]),
                kind=EffectKind(row["kind"]),
                target=str(row["target"]),
                claimed_at=float(row["claimed_at"]),
            )
            for row in rows
        )

    def done_for(self, summons: str) -> tuple[str, ...]:
        """The steps of ``summons`` that completed, so a resume can skip them."""
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT step FROM effects WHERE summons = ? AND done_at IS NOT NULL "
                    "ORDER BY done_at",
                    (summons,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(f"cannot read the effect ledger at {self.path}: {exc}") from exc
        return tuple(str(row["step"]) for row in rows)


def once(ledger: EffectLedger, effect: Effect) -> Iterator[Claim]:
    """Yield a claim if this effect has not happened, and close it on the way out.

    A generator rather than a context manager on purpose: a ``with`` block whose
    body might not run reads as if it always does, and "we skipped it because it
    already happened" is the case a caller most needs to see in its own control
    flow::

        for claim in once(ledger, effect):
            post(effect.body)
            ledger.complete(claim, result=url)

    Nothing is completed automatically. An act that raised must leave its claim
    open, because that is the difference between "did not happen" and "unknown".
    """
    claim = ledger.claim(effect)
    if claim is not None:
        yield claim


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "LEDGER_FILENAME",
    "LEDGER_SCHEMA_VERSION",
    "SCHEMA",
    "Claim",
    "EffectLedger",
    "EffectStatus",
    "LedgerError",
    "Pending",
    "once",
]
