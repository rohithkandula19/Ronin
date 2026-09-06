"""Which Ronin session a conversation out in the world is holding.

``docs/RETAINER.md`` §6.5, the missing half of resume. ``persistence.resume``
already replays a transcript back into an ``AgentState``; what nothing in the
tree does today is answer *which* transcript belongs to GitHub issue 258, or to
a Slack thread, or to a routine that fires every morning. That map is this file.

**Binding is get-or-create, and it is atomic**, for the reason
:mod:`ronin.retainer.ledger` claims before acting: two mentions posted a second
apart in the same thread must not start two sessions and answer twice from
different halves of the context. The insert and the read are one statement plus
one read of the row that won, so the primary key decides rather than the order
two callers happened to look.

**Losing this file is survivable, and that is the difference from the ledger.**
A forgotten binding costs continuity — the Retainer answers the next message with
no memory of the thread — where a forgotten *effect* costs a duplicate comment on
somebody's pull request. So the two live in separate stores with separate schema
versions: the consequences of losing them differ, and a store whose loss is
merely annoying should not be entangled with one whose loss is a visible mistake.
A schema mismatch still refuses rather than dropping, because "your Retainers all
forget every thread" is a decision for a human to take deliberately.

Depends on ``sqlite3`` from the standard library. No new dependency.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ronin.persistence.transcript import new_session_id, valid_session_id
from ronin.retainer.model import Channel

#: Bumped when the schema below changes. A mismatch refuses to open: forgetting
#: every thread is a human's decision, not a side effect of an upgrade.
THREADS_SCHEMA_VERSION: Final = 1

THREADS_FILENAME: Final = "threads.sqlite3"

DEFAULT_TIMEOUT_SECONDS: Final = 5.0

#: ``workspace`` is stored per binding rather than per Retainer because a
#: Retainer may hold more than one post, and resume needs the directory the
#: session's transcript actually lives beside — not the one it holds today.
SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS threads (
    retainer   TEXT NOT NULL,
    channel    TEXT NOT NULL,
    thread     TEXT NOT NULL,
    session    TEXT NOT NULL,
    workspace  TEXT NOT NULL DEFAULT '',
    bound_at   REAL NOT NULL,
    seen_at    REAL NOT NULL,
    PRIMARY KEY (retainer, channel, thread)
);
CREATE INDEX IF NOT EXISTS threads_recent ON threads (retainer, seen_at);
"""


class ThreadMapError(RuntimeError):
    """The thread map could not be read or written."""


@dataclass(frozen=True, slots=True)
class Binding:
    """One conversation and the session it is holding."""

    retainer: str
    channel: Channel
    thread: str
    session: str
    workspace: Path | None = None
    bound_at: float = 0.0
    seen_at: float = 0.0
    fresh: bool = False
    """True when this call created the binding. The caller greets a new thread
    differently from one it is continuing, and inferring that from timestamps is
    how a Retainer introduces itself twice."""


@dataclass(frozen=True, slots=True)
class ThreadMap:
    """External conversation ids to Ronin session ids, both ways."""

    path: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    clock: Callable[[], float] = time.time
    mint: Callable[[], str] = new_session_id
    """How a new session id is made. Injected so a test can pin one."""

    @classmethod
    def open(
        cls,
        directory: Path,
        *,
        filename: str = THREADS_FILENAME,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
        mint: Callable[[], str] = new_session_id,
    ) -> ThreadMap:
        directory.mkdir(parents=True, exist_ok=True)
        table = cls(path=directory / filename, timeout=timeout, clock=clock, mint=mint)
        table._prepare()
        return table

    # ------------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.path, timeout=self.timeout)
        except sqlite3.Error as exc:
            raise ThreadMapError(f"cannot open the thread map at {self.path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _prepare(self) -> None:
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("PRAGMA journal_mode=WAL")
                found = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if found and found != THREADS_SCHEMA_VERSION:
                    raise ThreadMapError(
                        f"the thread map at {self.path} is schema v{found} and this "
                        f"build understands v{THREADS_SCHEMA_VERSION}. Discarding it "
                        "makes every Retainer forget every conversation it is in, so "
                        "migrate it or move it aside deliberately."
                    )
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA user_version={THREADS_SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise ThreadMapError(f"cannot prepare the thread map at {self.path}: {exc}") from exc

    def _row(self, retainer: str, channel: Channel, thread: str) -> sqlite3.Row | None:
        with closing(self._connect()) as conn:
            row: sqlite3.Row | None = conn.execute(
                "SELECT * FROM threads WHERE retainer = ? AND channel = ? AND thread = ?",
                (retainer, channel.value, thread),
            ).fetchone()
        return row

    @staticmethod
    def _binding(row: sqlite3.Row, *, fresh: bool = False) -> Binding:
        workspace = str(row["workspace"])
        return Binding(
            retainer=str(row["retainer"]),
            channel=Channel(row["channel"]),
            thread=str(row["thread"]),
            session=str(row["session"]),
            workspace=Path(workspace) if workspace else None,
            bound_at=float(row["bound_at"]),
            seen_at=float(row["seen_at"]),
            fresh=fresh,
        )

    # ------------------------------------------------------------------ writing

    def bind(
        self,
        retainer: str,
        channel: Channel,
        thread: str,
        *,
        workspace: Path | None = None,
        session: str = "",
    ) -> Binding:
        """The session for this conversation, creating one only if there is none.

        Get-or-create rather than create, so a redelivered webhook and a second
        mention both land in the conversation that already exists. ``fresh`` on
        the result says which happened.

        ``session`` lets a caller supply the id — for a session it has already
        opened — and is validated, because the id is interpolated into a path by
        ``persistence.transcript`` and ``../../etc`` would name a file outside
        ``.ronin/sessions``.
        """
        if session and not valid_session_id(session):
            raise ThreadMapError(f"{session!r} is not a usable session id")
        now = self.clock()
        proposed = session or self.mint()
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "INSERT INTO threads "
                    "(retainer, channel, thread, session, workspace, bound_at, seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (
                        retainer,
                        channel.value,
                        thread,
                        proposed,
                        str(workspace) if workspace else "",
                        now,
                        now,
                    ),
                )
                created = cursor.rowcount == 1
                if not created:
                    conn.execute(
                        "UPDATE threads SET seen_at = ? "
                        "WHERE retainer = ? AND channel = ? AND thread = ?",
                        (now, retainer, channel.value, thread),
                    )
                row = conn.execute(
                    "SELECT * FROM threads WHERE retainer = ? AND channel = ? AND thread = ?",
                    (retainer, channel.value, thread),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ThreadMapError(f"cannot bind {channel.value} thread {thread}: {exc}") from exc
        return self._binding(row, fresh=created)

    def rebind(
        self,
        retainer: str,
        channel: Channel,
        thread: str,
        session: str,
        *,
        workspace: Path | None = None,
    ) -> Binding:
        """Point an existing conversation at a different session.

        For the case where a session was compacted past usefulness or abandoned
        and the thread should carry on in a new one. Refuses to invent a binding:
        a rebind of something that was never bound is a caller bug, and silently
        creating one hides it until the thread answers with no history.
        """
        if not valid_session_id(session):
            raise ThreadMapError(f"{session!r} is not a usable session id")
        try:
            with closing(self._connect()) as conn, conn:
                # COALESCE rather than two spellings of the statement: a SQL
                # string assembled by an `if` is one branch away from updating
                # the wrong column, and NULL already means "leave it".
                cursor = conn.execute(
                    "UPDATE threads SET session = ?, seen_at = ?, "
                    "workspace = COALESCE(?, workspace) "
                    "WHERE retainer = ? AND channel = ? AND thread = ?",
                    (
                        session,
                        self.clock(),
                        str(workspace) if workspace is not None else None,
                        retainer,
                        channel.value,
                        thread,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ThreadMapError(
                        f"{channel.value} thread {thread} is not bound for {retainer} — "
                        "bind() it rather than rebinding something that does not exist"
                    )
                row = conn.execute(
                    "SELECT * FROM threads WHERE retainer = ? AND channel = ? AND thread = ?",
                    (retainer, channel.value, thread),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ThreadMapError(f"cannot rebind {channel.value} thread {thread}: {exc}") from exc
        return self._binding(row)

    def forget(self, retainer: str, channel: Channel, thread: str) -> bool:
        """Drop a binding. The transcript is untouched; only the pointer goes."""
        try:
            with closing(self._connect()) as conn, conn:
                cursor = conn.execute(
                    "DELETE FROM threads WHERE retainer = ? AND channel = ? AND thread = ?",
                    (retainer, channel.value, thread),
                )
        except sqlite3.Error as exc:
            raise ThreadMapError(f"cannot forget {channel.value} thread {thread}: {exc}") from exc
        return cursor.rowcount == 1

    # ------------------------------------------------------------------ reading

    def lookup(self, retainer: str, channel: Channel, thread: str) -> Binding | None:
        """The binding for this conversation, or ``None``. Creates nothing."""
        try:
            row = self._row(retainer, channel, thread)
        except sqlite3.Error as exc:
            raise ThreadMapError(f"cannot read the thread map at {self.path}: {exc}") from exc
        return None if row is None else self._binding(row)

    def thread_for(self, session: str) -> Binding | None:
        """The conversation a session belongs to — the direction a reply needs.

        A run that ends holding only a session id still has to answer somebody,
        and without this it would have to be told where it came from twice.
        """
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM threads WHERE session = ? ORDER BY seen_at DESC LIMIT 1",
                    (session,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ThreadMapError(f"cannot read the thread map at {self.path}: {exc}") from exc
        return None if row is None else self._binding(row)

    def recent(self, retainer: str = "", *, limit: int = 20) -> tuple[Binding, ...]:
        """Bindings by when they were last spoken to, newest first."""
        sql = "SELECT * FROM threads"
        parameters: tuple[object, ...] = ()
        if retainer:
            sql += " WHERE retainer = ?"
            parameters = (retainer,)
        sql += " ORDER BY seen_at DESC, thread LIMIT ?"
        parameters = (*parameters, limit)
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, parameters).fetchall()
        except sqlite3.Error as exc:
            raise ThreadMapError(f"cannot read the thread map at {self.path}: {exc}") from exc
        return tuple(self._binding(row) for row in rows)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SCHEMA",
    "THREADS_FILENAME",
    "THREADS_SCHEMA_VERSION",
    "Binding",
    "ThreadMap",
    "ThreadMapError",
]
