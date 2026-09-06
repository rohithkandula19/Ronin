"""Asking a human for authority when the run that needs it has already ended.

``docs/RETAINER.md`` §3.3. :class:`~ronin.safety.policy.UnattendedAsker` answers
``no`` and says nobody was attached, which is the right floor for a terminal and
fatal for a teammate: an agent that can only do what needs no permission was
never trusted with anything. This module gives the third answer — *not yet*.

**The asker returns immediately, and the run ends.** ``Asker.ask`` is awaited
inside the turn, and its contract is that it must return; a ThreadAsker that
blocked for two days would hold a process open for two days and lose the answer
to the first restart. So :class:`ThreadAsker` records an :class:`Escalation` and
answers ``no`` — the run stops with ``exit_code 2``, which the headless path
already means as *an approval was requested and denied*. The reply, whenever it
comes, is a fresh summons that resumes from the recorded state.

**The refusal travels as ``detail``, never as ``feedback``.** That distinction is
load-bearing in :class:`~ronin.safety.policy.Answer`: ``feedback`` is a human's
own words, which the engine quotes back to the model as a correction to work
from. Nobody has spoken yet when an escalation is raised, so quoting anything
would be inventing it.

**An escalation outlives its session.** By the time somebody answers, the run
that asked has ended, so ``yes, for this session`` has no referent —
:class:`~ronin.retainer.model.Escalation` refuses it and the answer must be
*once* or *persist into the standing orders*.

**Who may answer is a question this module cannot answer.** Whether somebody has
write access to a repository is the adapter's knowledge and a network call; this
layer takes a predicate. It defaults to the Retainer's owner alone, so a caller
that forgets to pass one gets the narrow rule rather than an open door.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_hex
from typing import Final

from ronin.core.types import ApprovalRequest
from ronin.retainer.model import Escalation, EscalationState
from ronin.safety.policy import Answer, Outcome

ESCALATIONS_SCHEMA_VERSION: Final = 1

ESCALATIONS_FILENAME: Final = "escalations.sqlite3"

DEFAULT_TIMEOUT_SECONDS: Final = 5.0

#: How an unanswered escalation is described back to the model. A statement of
#: fact with no advice attached, in the register ``NO_HUMAN_ATTACHED`` uses:
#: ``PolicyEngine`` owns what the model should do about a refusal.
ASKED_AND_WAITING: Final = (
    "this needs an approval nobody has given yet, so it has been put to a human "
    "and this run is stopping here"
)

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS escalations (
    id          TEXT PRIMARY KEY,
    retainer    TEXT NOT NULL,
    thread      TEXT NOT NULL,
    tool        TEXT NOT NULL,
    request     TEXT NOT NULL,
    session     TEXT NOT NULL DEFAULT '',
    checkpoint  TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL,
    answer      TEXT,
    answered_by TEXT NOT NULL DEFAULT '',
    raised_at   REAL NOT NULL,
    settled_at  REAL
);
CREATE INDEX IF NOT EXISTS escalations_open ON escalations (retainer, state, raised_at);
"""


class EscalationError(RuntimeError):
    """An escalation could not be recorded, read, or answered."""


class NotAllowedToAnswer(EscalationError):
    """Somebody without the authority tried to answer.

    Its own type because a caller must be able to tell "you may not decide this"
    from "the store is broken" — the first is a reply to post in the thread, the
    second is a page.
    """


def owner_only(owner: str) -> Callable[[str], bool]:
    """The default authority: the Retainer's own owner and nobody else."""

    def allowed(who: str) -> bool:
        return bool(who) and who == owner

    return allowed


def nobody(_who: str) -> bool:
    """No authority at all. What an unconfigured store falls back to."""
    return False


@dataclass(frozen=True, slots=True)
class EscalationStore:
    """Where escalations wait between the run that raised one and the answer."""

    path: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    clock: Callable[[], float] = time.time

    @classmethod
    def open(
        cls,
        directory: Path,
        *,
        filename: str = ESCALATIONS_FILENAME,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> EscalationStore:
        directory.mkdir(parents=True, exist_ok=True)
        store = cls(path=directory / filename, timeout=timeout, clock=clock)
        store._prepare()
        return store

    # ------------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.path, timeout=self.timeout)
        except sqlite3.Error as exc:
            raise EscalationError(
                f"cannot open the escalation store at {self.path}: {exc}"
            ) from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _prepare(self) -> None:
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("PRAGMA journal_mode=WAL")
                found = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if found and found != ESCALATIONS_SCHEMA_VERSION:
                    raise EscalationError(
                        f"the escalation store at {self.path} is schema v{found} and "
                        f"this build understands v{ESCALATIONS_SCHEMA_VERSION}. It "
                        "holds runs waiting on a human, so discarding it strands them: "
                        "migrate it or move it aside deliberately."
                    )
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA user_version={ESCALATIONS_SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise EscalationError(
                f"cannot prepare the escalation store at {self.path}: {exc}"
            ) from exc

    @staticmethod
    def _record(row: sqlite3.Row) -> Escalation:
        answer = row["answer"]
        return Escalation(
            id=str(row["id"]),
            retainer=str(row["retainer"]),
            thread=str(row["thread"]),
            tool=str(row["tool"]),
            request=str(row["request"]),
            session=str(row["session"]),
            checkpoint=str(row["checkpoint"]),
            state=EscalationState(row["state"]),
            answer=Outcome(answer) if answer else None,
            answered_by=str(row["answered_by"]),
        )

    # ------------------------------------------------------------------ writing

    def raise_(self, escalation: Escalation) -> Escalation:
        """Record a new escalation. Refuses to overwrite one that already exists."""
        if not escalation.open:
            raise EscalationError(f"escalation {escalation.id} is not open — nothing to raise")
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "INSERT INTO escalations "
                    "(id, retainer, thread, tool, request, session, checkpoint, state, "
                    "raised_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (
                        escalation.id,
                        escalation.retainer,
                        escalation.thread,
                        escalation.tool,
                        escalation.request,
                        escalation.session,
                        escalation.checkpoint,
                        escalation.state.value,
                        self.clock(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise EscalationError(f"escalation {escalation.id} already exists")
        except sqlite3.Error as exc:
            raise EscalationError(f"cannot record escalation {escalation.id}: {exc}") from exc
        return escalation

    def answer(
        self,
        escalation_id: str,
        outcome: Outcome,
        *,
        by: str,
        may_answer: Callable[[str], bool] = nobody,
    ) -> Escalation:
        """Apply a human's answer, checking that they were entitled to give it.

        ``by`` must be a *verified* identity from the adapter, not a display name
        off the message: the whole authority check is worthless if the thing it
        checks is attacker-supplied text.
        """
        if not by:
            raise NotAllowedToAnswer("an answer with no verified identity is not an answer")
        if not may_answer(by):
            raise NotAllowedToAnswer(f"{by} is not allowed to answer escalation {escalation_id}")
        current = self.lookup(escalation_id)
        if current is None:
            raise EscalationError(f"no escalation {escalation_id}")
        # Validated here so an impossible answer is refused before it is stored.
        # The record's own guards raise ValueError; they are translated so a
        # caller of the store has one error type to handle rather than needing
        # to know which layer objected.
        try:
            settled = current.resolved(outcome, by=by)
        except ValueError as exc:
            raise EscalationError(str(exc)) from None
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "UPDATE escalations SET state = ?, answer = ?, answered_by = ?, "
                    "settled_at = ? WHERE id = ? AND state = ?",
                    (
                        settled.state.value,
                        outcome.value,
                        by,
                        self.clock(),
                        escalation_id,
                        EscalationState.OPEN.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise EscalationError(
                        f"escalation {escalation_id} was answered by somebody else first"
                    )
        except sqlite3.Error as exc:
            raise EscalationError(f"cannot answer escalation {escalation_id}: {exc}") from exc
        return settled

    def expire(self, escalation_id: str) -> Escalation:
        """Close an escalation nobody answered. It never becomes a yes by ageing."""
        current = self.lookup(escalation_id)
        if current is None:
            raise EscalationError(f"no escalation {escalation_id}")
        if not current.open:
            raise EscalationError(f"escalation {escalation_id} is already {current.state.value}")
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "UPDATE escalations SET state = ?, settled_at = ? WHERE id = ?",
                    (EscalationState.EXPIRED.value, self.clock(), escalation_id),
                )
        except sqlite3.Error as exc:
            raise EscalationError(f"cannot expire escalation {escalation_id}: {exc}") from exc
        return self._replace_state(current, EscalationState.EXPIRED)

    @staticmethod
    def _replace_state(escalation: Escalation, state: EscalationState) -> Escalation:
        return Escalation(
            id=escalation.id,
            retainer=escalation.retainer,
            thread=escalation.thread,
            tool=escalation.tool,
            request=escalation.request,
            session=escalation.session,
            checkpoint=escalation.checkpoint,
            state=state,
        )

    # ------------------------------------------------------------------ reading

    def lookup(self, escalation_id: str) -> Escalation | None:
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM escalations WHERE id = ?", (escalation_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise EscalationError(f"cannot read the escalation store: {exc}") from exc
        return None if row is None else self._record(row)

    def waiting(self, retainer: str = "") -> tuple[Escalation, ...]:
        """Every open escalation, oldest first — the queue a human works through."""
        sql = "SELECT * FROM escalations WHERE state = ?"
        parameters: tuple[str, ...] = (EscalationState.OPEN.value,)
        if retainer:
            sql += " AND retainer = ?"
            parameters = (*parameters, retainer)
        sql += " ORDER BY raised_at, id"
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, parameters).fetchall()
        except sqlite3.Error as exc:
            raise EscalationError(f"cannot read the escalation store: {exc}") from exc
        return tuple(self._record(row) for row in rows)


def _default_mint() -> str:
    """A short, unguessable escalation id. It appears in a public thread."""
    return f"esc-{token_hex(8)}"


@dataclass(frozen=True, slots=True)
class ThreadAsker:
    """An :class:`~ronin.safety.policy.Asker` that records the question and stops.

    Satisfies the protocol without ever blocking: it always answers ``no``, and
    the ``no`` means *not yet*. Whether the run should be resumed later is the
    caller's business, and the escalation it left behind is how.
    """

    store: EscalationStore
    retainer: str
    thread: str
    session: str = ""
    checkpoint: str = ""
    mint: Callable[[], str] = _default_mint
    raised: list[str] = field(default_factory=list)
    """Ids raised by this asker, in order, so the caller can post them."""

    async def ask(self, request: ApprovalRequest) -> Answer:
        escalation = Escalation(
            id=self.mint(),
            retainer=self.retainer,
            thread=self.thread,
            tool=request.name,
            request=request.rendered,
            session=self.session,
            checkpoint=self.checkpoint,
        )
        self.store.raise_(escalation)
        self.raised.append(escalation.id)
        return Answer(outcome=Outcome.NO, detail=ASKED_AND_WAITING)


__all__ = [
    "ASKED_AND_WAITING",
    "DEFAULT_TIMEOUT_SECONDS",
    "ESCALATIONS_FILENAME",
    "ESCALATIONS_SCHEMA_VERSION",
    "SCHEMA",
    "EscalationError",
    "EscalationStore",
    "NotAllowedToAnswer",
    "ThreadAsker",
    "nobody",
    "owner_only",
]
