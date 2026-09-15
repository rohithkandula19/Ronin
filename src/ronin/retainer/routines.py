"""Routines: a Retainer waking itself, on the same path a mention uses.

``docs/RETAINER.md`` §8 step 9, deliberately last, because a scheduler
multiplies whatever is already wrong.

**A routine emits the same Summons a mention does.** That is the whole safety
argument: a routine cannot reach anything a person addressing the Retainer could
not, because by the time the plane above sees it there is nothing to distinguish
the two except a :class:`~ronin.retainer.model.SummonsKind` kept for the audit
log. There is no privileged path and no second set of rules to keep in step.

**A routine can only wake its own Retainer.** :class:`Routine` has one
``retainer`` field and no target, so "have Sentry poke Scout" is not something
you can express. That is not a cap that could be raised — it is unrepresentable,
which is the only version of this rule that survives somebody being clever. The
field evidence is blunt: four standing agents messaging each other consumed a
weekly budget in twenty-one seconds, and every one of those messages was a
billable turn.

**A daemon that was down does not catch up.** If a laptop sleeps for a week, an
hourly routine is *overdue*, not owed a hundred and sixty-eight runs. Firing
advances the clock to now rather than to ``last_fired + interval``, so the
backlog is discarded by construction. Getting this wrong is not a small bug: it
is the runaway-spend failure, arriving all at once, the moment a machine wakes.

**A fire is identified by its window, not its instant.** The summons id is
derived with the window as the nonce, so two attempts to fire the same routine in
the same window produce the same id — and the effect ledger, which keys on that
id, makes the second one a no-op instead of a second comment. A scheduler that
merely tried not to double-fire would eventually double-fire.

Depends on ``sqlite3`` from the standard library. No new dependency.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ronin.retainer.model import SLUG, Channel, Summons, SummonsKind, summons_id

ROUTINES_SCHEMA_VERSION: Final = 1

ROUTINES_FILENAME: Final = "routines.sqlite3"

DEFAULT_TIMEOUT_SECONDS: Final = 5.0

#: The shortest interval a routine may have. A Retainer that needs to react
#: faster than this wants a webhook, not a schedule — and the floor is what stops
#: "every minute" from being a plausible-looking way to spend a budget.
MINIMUM_INTERVAL_SECONDS: Final = 300

#: How many routines one Retainer may hold. Bounded because an unbounded fan-out
#: of scheduled work is the same failure as bot-to-bot chatter with extra steps.
MAX_ROUTINES_PER_RETAINER: Final = 20

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS routines (
    id          TEXT PRIMARY KEY,
    retainer    TEXT NOT NULL,
    channel     TEXT NOT NULL,
    thread      TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    interval_s  INTEGER NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_fired  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS routines_of ON routines (retainer, enabled);
"""


class RoutineError(RuntimeError):
    """A routine could not be stored, read, or fired."""


@dataclass(frozen=True, slots=True)
class Routine:
    """One standing instruction and when it runs.

    Note what is *not* here: any way to name a different Retainer. See the
    module docstring — the absence is the rule.
    """

    id: str
    retainer: str
    channel: Channel
    thread: str
    """Where the answer goes. A routine that reports nowhere is a routine
    nobody can tell has broken."""
    prompt: str
    interval_s: int
    enabled: bool = True
    last_fired: float = 0.0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Routine.id is required")
        if not SLUG.match(self.retainer):
            raise ValueError(f"Routine.retainer must be a slug, got {self.retainer!r}")
        if not self.thread:
            raise ValueError("Routine.thread is required — a routine must report somewhere")
        if not self.prompt.strip():
            raise ValueError("Routine.prompt is required — there is nothing to do without it")
        if self.interval_s < MINIMUM_INTERVAL_SECONDS:
            raise ValueError(
                f"Routine.interval_s must be at least {MINIMUM_INTERVAL_SECONDS}s; "
                f"{self.interval_s}s is fast enough to be a webhook instead"
            )

    def window(self, now: float) -> int:
        """Which interval-sized slot ``now`` falls in. The identity of one fire."""
        return int(now // self.interval_s)

    def due(self, now: float) -> bool:
        """Whether this routine should run. A disabled one never is."""
        if not self.enabled:
            return False
        if self.last_fired <= 0:
            return True
        return self.window(now) > self.window(self.last_fired)

    def overdue_by(self, now: float) -> int:
        """How many windows have passed unrun. Reported, never acted on.

        Exists so a Retainer can *say* it missed a night rather than silently
        pretending it did not — and so nobody is tempted to loop over it.
        """
        if self.last_fired <= 0:
            return 0
        return max(0, self.window(now) - self.window(self.last_fired) - 1)

    def summons(self, now: float) -> Summons:
        """The request this fire makes. Identical in shape to a mention's."""
        return Summons(
            retainer=self.retainer,
            kind=SummonsKind.ROUTINE,
            channel=self.channel,
            thread=self.thread,
            text=self.prompt,
            actor=f"routine:{self.id}",
        )

    def fire_id(self, now: float) -> str:
        """A deterministic id for this fire, keyed on **this routine and** its window.

        Two attempts in one window produce one id, so the effect ledger turns
        the second into a no-op rather than a second comment.

        The routine's own id is in the nonce, and that is not belt-and-braces.
        :func:`~ronin.retainer.model.summons_id` hashes what was asked and where,
        deliberately not *who* asked — ``Summons.actor`` is an untrusted display
        name, and a renamed user must not turn a redelivery into a new request.
        The consequence is that two routines with the same prompt in the same
        thread produce identical summonses, so without the id here they would
        share a fire id, and the ledger would suppress one of them forever
        rather than visibly. The nonce exists for exactly this case.
        """
        return summons_id(self.summons(now), nonce=f"{self.id}:{self.window(now)}")


@dataclass(frozen=True, slots=True)
class RoutineStore:
    """Every routine, and when each last ran."""

    path: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    clock: Callable[[], float] = time.time

    @classmethod
    def open(
        cls,
        directory: Path,
        *,
        filename: str = ROUTINES_FILENAME,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> RoutineStore:
        directory.mkdir(parents=True, exist_ok=True)
        store = cls(path=directory / filename, timeout=timeout, clock=clock)
        store._prepare()
        return store

    # ------------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.path, timeout=self.timeout)
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot open the routine store at {self.path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _prepare(self) -> None:
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("PRAGMA journal_mode=WAL")
                found = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if found and found != ROUTINES_SCHEMA_VERSION:
                    raise RoutineError(
                        f"the routine store at {self.path} is schema v{found} and this "
                        f"build understands v{ROUTINES_SCHEMA_VERSION}. Discarding it "
                        "stops every scheduled run silently, which is the worst way "
                        "to find out: migrate it or move it aside deliberately."
                    )
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA user_version={ROUTINES_SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot prepare the routine store at {self.path}: {exc}") from exc

    @staticmethod
    def _record(row: sqlite3.Row) -> Routine:
        return Routine(
            id=str(row["id"]),
            retainer=str(row["retainer"]),
            channel=Channel(row["channel"]),
            thread=str(row["thread"]),
            prompt=str(row["prompt"]),
            interval_s=int(row["interval_s"]),
            enabled=bool(row["enabled"]),
            last_fired=float(row["last_fired"]),
        )

    # ------------------------------------------------------------------ writing

    def add(self, routine: Routine) -> Routine:
        """Store a routine, refusing a duplicate id or an over-full Retainer."""
        existing = len(self.of(routine.retainer))
        if existing >= MAX_ROUTINES_PER_RETAINER:
            raise RoutineError(
                f"{routine.retainer} already holds {existing} routines, the maximum "
                f"of {MAX_ROUTINES_PER_RETAINER} — an unbounded fan-out of scheduled "
                "work is bot-to-bot chatter with extra steps"
            )
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "INSERT INTO routines "
                    "(id, retainer, channel, thread, prompt, interval_s, enabled, last_fired) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (
                        routine.id,
                        routine.retainer,
                        routine.channel.value,
                        routine.thread,
                        routine.prompt,
                        routine.interval_s,
                        int(routine.enabled),
                        routine.last_fired,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RoutineError(f"routine {routine.id} already exists")
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot store routine {routine.id}: {exc}") from exc
        return routine

    def enable(self, routine_id: str, *, enabled: bool = True) -> Routine:
        """Turn a routine on or off without losing when it last ran."""
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "UPDATE routines SET enabled = ? WHERE id = ?",
                    (int(enabled), routine_id),
                )
                if cursor.rowcount != 1:
                    raise RoutineError(f"no routine {routine_id}")
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot update routine {routine_id}: {exc}") from exc
        settled = self.lookup(routine_id)
        assert settled is not None  # just updated it under the same lock discipline
        return settled

    def remove(self, routine_id: str) -> bool:
        """Delete a routine. Reports whether there was one."""
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot remove routine {routine_id}: {exc}") from exc
        return cursor.rowcount == 1

    def mark_fired(self, routine: Routine, now: float) -> Routine:
        """Record that a routine ran, advancing to **now** and not by one interval.

        The difference is the whole no-catch-up rule: advancing by an interval
        would leave a machine that slept for a week owing a hundred and sixty-
        eight runs, and it would deliver them.
        """
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "UPDATE routines SET last_fired = ? WHERE id = ? AND last_fired < ?",
                    (now, routine.id, now),
                )
                fired = cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot mark routine {routine.id} fired: {exc}") from exc
        if not fired:
            raise RoutineError(
                f"routine {routine.id} was already fired at or after {now} — "
                "another scheduler got there first"
            )
        return Routine(
            id=routine.id,
            retainer=routine.retainer,
            channel=routine.channel,
            thread=routine.thread,
            prompt=routine.prompt,
            interval_s=routine.interval_s,
            enabled=routine.enabled,
            last_fired=now,
        )

    # ------------------------------------------------------------------ reading

    def lookup(self, routine_id: str) -> Routine | None:
        try:
            with closing(self._connect()) as conn:
                row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot read the routine store: {exc}") from exc
        return None if row is None else self._record(row)

    def of(self, retainer: str) -> tuple[Routine, ...]:
        """Every routine of one Retainer, enabled or not, by id."""
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT * FROM routines WHERE retainer = ? ORDER BY id", (retainer,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot read the routine store: {exc}") from exc
        return tuple(self._record(row) for row in rows)

    def due(self, now: float, *, retainer: str = "") -> tuple[Routine, ...]:
        """Every routine that should run at ``now``, by id.

        Filtering in Python rather than SQL on purpose: :meth:`Routine.due` is
        the one definition of due-ness, and a second copy in a ``WHERE`` clause
        is the copy that would drift.
        """
        sql = "SELECT * FROM routines WHERE enabled = 1"
        parameters: tuple[str, ...] = ()
        if retainer:
            sql += " AND retainer = ?"
            parameters = (retainer,)
        sql += " ORDER BY id"
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, parameters).fetchall()
        except sqlite3.Error as exc:
            raise RoutineError(f"cannot read the routine store: {exc}") from exc
        return tuple(r for r in (self._record(row) for row in rows) if r.due(now))


def fire(store: RoutineStore, routine: Routine, now: float) -> tuple[Summons, Routine]:
    """Claim a routine's turn and return the request it makes.

    Marks it fired *before* handing back the summons, so a crash between the two
    loses a run rather than repeating one — and the effect ledger, keyed on
    :meth:`Routine.fire_id`, makes even a repeat harmless.
    """
    advanced = store.mark_fired(routine, now)
    return routine.summons(now), advanced


def sequence_of(routines: Sequence[Routine]) -> tuple[str, ...]:
    """The ids in the order a scheduler would run them. Stable, for the log."""
    return tuple(routine.id for routine in routines)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_ROUTINES_PER_RETAINER",
    "MINIMUM_INTERVAL_SECONDS",
    "ROUTINES_FILENAME",
    "ROUTINES_SCHEMA_VERSION",
    "SCHEMA",
    "Routine",
    "RoutineError",
    "RoutineStore",
    "fire",
    "sequence_of",
]
