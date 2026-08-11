"""The sqlite session index: a *derived* view of the jsonl transcripts.

The transcript is the truth. This file is a cache of it, and every decision below
follows from that one sentence.

**Why an index at all.** :func:`~ronin.persistence.transcript.list_sessions` reads one
sidecar per session, which is O(sessions) — fine for a picker, useless for two
questions a real user asks after a month of work: *"which sessions in this directory
cost the most"* and *"which session was the one where I fixed the pagination bug"*.
The first is an ``ORDER BY`` the filesystem cannot do; the second is full-text search
over text that only exists inside the logs. sqlite with fts5 answers both, and is in
the standard library.

**Why it may be deleted at any time.** Nothing here is authoritative, so
``rm .ronin/sessions/index.sqlite3`` costs a rebuild and loses nothing. That is not a
disclaimer, it is the design: a second source of truth would mean reconciling two
files that disagree about your money, and the way to not have that problem is to make
one of them regenerable. :meth:`SessionIndex.rebuild` is the whole recovery procedure,
:data:`INDEX_SCHEMA_VERSION` is stamped in ``PRAGMA user_version``, and a mismatch
**drops and rebuilds** rather than migrating — a cache with a migration story is a
cache pretending to be a database.

**Why writes never raise and reads do.** :meth:`record` is called from the agent loop
at a turn boundary. A failure there — a full disk, a locked database, a sqlite build
without fts5 — must not end a session that is otherwise fine, so it is recorded in
:attr:`problems` and the turn continues; the transcript, which *is* the truth, was
already fsynced by then. :meth:`search` and :meth:`recent` are the opposite: a human
asked a question, and an empty list is a lie that reads exactly like "no matches".
Those raise :class:`SessionIndexError`, and the caller falls back to
``list_sessions``.

**Why the query is rebuilt rather than forwarded.** fts5 ``MATCH`` takes an expression
in its own syntax, not a phrase: an apostrophe in ``don't`` raises
``OperationalError: unterminated string``, and ``NOT`` in the middle of a sentence
silently inverts the search. Every terminal-typed query would therefore be a coin
flip between a crash and a wrong answer. :func:`fts_query` extracts bare words and
quotes each one, so any string a user can type is a valid, predictable query.

**What is indexed, and what deliberately is not.** Per turn: the user's prompt and the
assistant's prose. Tool *arguments* and tool output are not — they are the bulk of a
transcript's bytes, they are where fetched pages and file contents land, and searching
them would make the index rival the logs in size while returning a matched line of
somebody else's HTML. The prompt is what people remember, so the prompt is what is
searchable.

The user's prompt is not an ``Event``. It reaches this module through
``TurnEnd.agent_state.messages`` — the loop records state, not the question — which is
why :func:`searchable_turns` reads the last ``USER`` message of each turn's recorded
state rather than looking for an event that does not exist.

**Sensitivity.** This file contains prompt text in plain rows, so it is exactly as
sensitive as the transcripts beside it and lives in the same ``.ronin/sessions``
directory. It is not a new disclosure — it is a second copy of one, and it inherits
the directory's protection rather than inventing its own.

Depends on ``sqlite3`` and ``json`` from the standard library. No new dependency:
sqlite ships with Python, WAL and fts5 included in every build this project supports —
and where fts5 is missing, metadata still indexes and only :meth:`search` refuses.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ..core.types import Event, Role, Text
from .resume import ReplayTurn, fold_turns
from .transcript import (
    SUFFIX,
    SessionMeta,
    TranscriptError,
    list_sessions,
    read_events,
    valid_session_id,
)

#: The index lives beside the logs it describes, and is named for what it is.
INDEX_FILENAME: Final = "index.sqlite3"

#: Bumped when the schema below changes. A mismatch drops every table and rebuilds
#: from the transcripts, because this file is a cache — see the module docstring.
INDEX_SCHEMA_VERSION: Final = 1

#: How long to wait for another Ronin's write lock before giving up. WAL means
#: readers never block, so this is only ever two *writers* at one turn boundary —
#: brief by construction, which is why a few seconds is generous rather than a guess.
DEFAULT_TIMEOUT_SECONDS: Final = 5.0

#: The one row per session that makes ``ORDER BY cost`` possible.
#:
#: ``files_touched`` and ``checkpoint_ids`` are JSON arrays in ``TEXT`` columns rather
#: than child tables. Normalising them would buy "which sessions touched this file",
#: which nothing asks for yet; the JSON keeps the round trip to
#: :class:`~ronin.persistence.transcript.SessionMeta` exact and the writer trivial. The
#: shape is safe to choose loosely precisely because the index is derived: changing it
#: later is a version bump and a rebuild, not a migration.
SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id       TEXT PRIMARY KEY,
    cwd              TEXT NOT NULL DEFAULT '',
    model            TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    started_at       REAL NOT NULL DEFAULT 0,
    updated_at       REAL NOT NULL DEFAULT 0,
    turns            INTEGER NOT NULL DEFAULT 0,
    events           INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    files_touched    TEXT NOT NULL DEFAULT '[]',
    checkpoint_ids   TEXT NOT NULL DEFAULT '[]',
    unreadable       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sessions_recent ON sessions (updated_at DESC);
CREATE INDEX IF NOT EXISTS sessions_cwd ON sessions (cwd);
"""

#: Separate from :data:`SCHEMA` because it is the one statement that can fail on a
#: working sqlite: a build compiled without fts5. Metadata indexing must survive that.
FTS_SCHEMA: Final = """
CREATE VIRTUAL TABLE IF NOT EXISTS turn_text USING fts5(
    session_id UNINDEXED,
    turn       UNINDEXED,
    role       UNINDEXED,
    body,
    tokenize = 'unicode61'
);
"""

#: Words in a query. Anything else a user types is punctuation to fts5, and
#: punctuation is what makes ``MATCH`` raise. Both the ASCII apostrophe and U+2019 are
#: kept *inside* a word, because a terminal receives whichever one the user's keyboard
#: or editor produced and splitting there would search for ``don`` and ``t``. U+2019 is
#: written as an escape rather than pasted: an invisible distinction between two
#: apostrophes is exactly the kind of thing a later reader silently "fixes".
_WORD: Final = re.compile("[^\\W_]+(?:['\\u2019][^\\W_]+)*", re.UNICODE)


class SessionIndexError(RuntimeError):
    """A question the index could not answer.

    Raised by the *reads* only. A write failure is not an error a session should die
    of — see :attr:`SessionIndex.problems` and the module docstring.
    """


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One matching turn, with enough context to choose a session from a list."""

    session_id: str
    turn: int
    role: str
    snippet: str
    cwd: str = ""
    updated_at: float = 0.0


@dataclass(frozen=True, slots=True)
class RebuildReport:
    """What a rebuild found. ``skipped`` names files and why, never silently drops.

    A transcript from another schema version lands in ``skipped``, not in an
    exception: one unreadable session must not stop the other nine hundred from being
    indexed, and the reason is what tells the user it is a version problem rather than
    a corrupt file.
    """

    indexed: int = 0
    removed: int = 0
    skipped: tuple[str, ...] = ()

    def summary(self) -> str:
        parts = [f"{self.indexed} session(s) indexed"]
        if self.removed:
            parts.append(f"{self.removed} stale row(s) dropped")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return ", ".join(parts)


def fts_query(text: str) -> str:
    """A user's words as a safe fts5 ``MATCH`` expression, or ``""`` for no words.

    Every word is extracted and re-quoted, which does three things at once: an
    apostrophe stops raising ``unterminated string``, a stray ``NOT``/``OR``/``*``
    stops being an operator the user did not intend, and the result is an implicit AND
    over the words — "all of these terms", which is what a search box means to
    everyone who has ever used one.

    Returning ``""`` rather than raising on a wordless query keeps the decision with
    the caller: a CLI reports "nothing to search for", and a rebuild does not care.
    """
    words = _WORD.findall(text)
    return " ".join('"' + word.replace('"', '""') + '"' for word in words)


def searchable_turns(events: Sequence[Event]) -> tuple[tuple[int, str, str], ...]:
    """``(turn_index, role, body)`` for every turn with text worth searching.

    Built on :func:`~ronin.persistence.resume.fold_turns` — the same fold the exporters
    use — so search sees the session the way a human reading the Markdown export
    would, with ``StreamReset`` already applied. Text discarded by a reset is text the
    model retracted, and indexing it would surface sessions by sentences they do not
    contain.
    """
    rows: list[tuple[int, str, str]] = []
    for turn in fold_turns(events):
        prompt = _prompt_of(turn)
        if prompt:
            rows.append((turn.index, Role.USER.value, prompt))
        if turn.text.strip():
            rows.append((turn.index, Role.ASSISTANT.value, turn.text.strip()))
    return tuple(rows)


def _prompt_of(turn: ReplayTurn) -> str:
    """The question that drove this turn, out of the state its end recorded.

    ``agent_state.messages`` is the *whole* conversation so far, so the prompt for
    this turn is the last ``USER`` message in it — taking the first would index turn
    one's question against every turn in the session. ``None`` state (a replayed
    recording, which has no state to hand back) simply yields nothing.
    """
    state = turn.end.agent_state if turn.end is not None else None
    if state is None:
        return ""
    for message in reversed(state.messages):
        if message.role is Role.USER:
            return "\n".join(
                block.text for block in message.content_blocks if isinstance(block, Text)
            ).strip()
    return ""


def index_path(directory: Path, *, filename: str = INDEX_FILENAME) -> Path:
    """Where the index lives for a given sessions directory."""
    return directory / filename


@dataclass(eq=False)
class SessionIndex:
    """The sqlite index over one sessions directory.

    Connections are opened per operation rather than held, matching
    :class:`ronin.providers.accounting.Ledger` and for its reason: an agent session is
    long-lived, and a held sqlite handle turns a crash into a lock somebody has to
    clear by hand. WAL mode is a property of the *file*, so it survives the
    reconnect — it is set once, when the schema is created.
    """

    path: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    #: Write failures, in order, deduplicated. The loop keeps running; the user is
    #: told once. Same discipline as the transcript recorder in ``cli/stream.py``,
    #: which learned it the same way: four hundred identical warnings tell you nothing.
    problems: list[str] = field(default_factory=list)
    _searchable: bool | None = field(default=None, repr=False)

    @classmethod
    def open(
        cls,
        directory: Path,
        *,
        filename: str = INDEX_FILENAME,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> SessionIndex:
        """Create (or adopt) the index for ``directory``, schema ready to use.

        Never raises for a database it cannot prepare: a broken index degrades to one
        that records problems and answers nothing, because the alternative is a
        session that will not start because of its cache.
        """
        index = cls(path=index_path(directory, filename=filename), timeout=timeout)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            index._prepare()
        except (OSError, sqlite3.Error) as exc:
            index._fail(f"could not prepare the session index ({exc})")
        return index

    # ------------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        return conn

    def _fail(self, message: str) -> None:
        """Record a write problem once. Order preserved; repeats dropped."""
        if message not in self.problems:
            self.problems.append(message)

    @property
    def degraded(self) -> bool:
        """Whether anything has gone wrong with this index in this process."""
        return bool(self.problems)

    def _prepare(self, *, recreate_if_corrupt: bool = True) -> None:
        """Apply the schema, replacing the file outright if it is not a database.

        The replacement is what makes ``--reindex`` a real repair. Without it the
        recovery procedure raised on exactly the input it exists to fix: a truncated or
        garbage ``index.sqlite3``, which is what a killed process or a full disk leaves
        behind. Deleting is safe here and nowhere else in this package — the file is a
        cache of the transcripts, which is the whole reason it was allowed to exist.

        Only a file sqlite says is *not a database* is removed. A lock (``OperationalError``,
        raised when another Ronin is mid-write) is left completely alone: deleting a
        database another process has open would turn a moment's contention into two
        corrupt indexes. Found by a test that expected a soft failure and got a
        ``DatabaseError``.
        """
        try:
            self._apply_schema()
        except sqlite3.OperationalError:
            raise  # a lock, or an unwritable path: not ours to delete
        except sqlite3.DatabaseError:
            if not recreate_if_corrupt:
                raise
            self._replace_corrupt_file()
            self._prepare(recreate_if_corrupt=False)

    def _replace_corrupt_file(self) -> None:
        """Delete an index sqlite will not open, plus its WAL sidecars."""
        self._fail(
            f"the session index at {self.path} was not a readable database and has been "
            f"rebuilt from the transcripts; nothing was lost, because it is a cache of them"
        )
        for suffix in ("", "-wal", "-shm"):
            candidate = self.path.with_name(self.path.name + suffix)
            # Suppressed rather than reported: this runs while already handling a broken
            # index, and the caller learns the outcome from whether the retry succeeds.
            with suppress(OSError):
                candidate.unlink(missing_ok=True)

    def _apply_schema(self) -> None:
        """Create the tables, dropping everything first on a version mismatch."""
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            found = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if found and found != INDEX_SCHEMA_VERSION:
                # A cache from another build. Dropped rather than migrated, then
                # refilled from the transcripts by the caller's next rebuild — which
                # `recent`/`search` cannot do for it, so the drop must leave a *valid*
                # empty index rather than a half-old one.
                conn.executescript("DROP TABLE IF EXISTS sessions; DROP TABLE IF EXISTS turn_text;")
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version={INDEX_SCHEMA_VERSION}")
            self._searchable = self._try_fts(conn)

    def _try_fts(self, conn: sqlite3.Connection) -> bool:
        """Create the fts5 table, or report that this sqlite has no fts5 and go on."""
        try:
            conn.executescript(FTS_SCHEMA)
        except sqlite3.Error as exc:
            self._fail(
                f"this Python's sqlite has no fts5 module ({exc}), so session metadata "
                f"is indexed but full-text search is unavailable"
            )
            return False
        return True

    @property
    def searchable(self) -> bool:
        """Whether full-text search works here (i.e. sqlite was built with fts5)."""
        if self._searchable is None:
            try:
                with closing(self._connect()) as conn, conn:
                    self._searchable = self._try_fts(conn)
            except sqlite3.Error as exc:
                self._fail(f"could not open the session index ({exc})")
                self._searchable = False
        return self._searchable

    # -------------------------------------------------------------------- writing

    def record_turn(self, meta: SessionMeta, events: Sequence[Event] = ()) -> bool:
        """Satisfy :class:`ronin.persistence.transcript.Indexer` for a finished turn.

        The log writer hands over raw events; deciding which of them is searchable text
        belongs here, next to the schema that stores it. Never raises, per that
        protocol's contract and :meth:`record`'s.
        """
        return self.record(meta, searchable_turns(events))

    def record(self, meta: SessionMeta, turns: Sequence[tuple[int, str, str]] = ()) -> bool:
        """Upsert one session's row, and replace the text of the turns named in ``turns``.

        Returns whether the write landed. **Never raises** — see the module docstring:
        this runs at a turn boundary, after the transcript is already durable, and a
        cache that can end a session is worse than no cache.

        Text is replaced *per turn index*, not per session. That is what lets the live
        path be incremental: a session's tenth turn rewrites ten rows, not the whole
        history, so the cost of indexing does not grow with the length of the
        conversation. Delete-then-insert rather than an upsert because fts5 has no
        unique key to conflict on, and it makes a turn recorded twice by a resume
        idempotent rather than doubled.
        """
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        session_id, cwd, model, title, started_at, updated_at,
                        turns, events, cost_usd, duration_seconds,
                        files_touched, checkpoint_ids, unreadable
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (session_id) DO UPDATE SET
                        cwd=excluded.cwd,
                        model=excluded.model,
                        title=excluded.title,
                        started_at=excluded.started_at,
                        updated_at=excluded.updated_at,
                        turns=excluded.turns,
                        events=excluded.events,
                        cost_usd=excluded.cost_usd,
                        duration_seconds=excluded.duration_seconds,
                        files_touched=excluded.files_touched,
                        checkpoint_ids=excluded.checkpoint_ids,
                        unreadable=excluded.unreadable
                    """,
                    (
                        meta.session_id,
                        meta.cwd,
                        meta.model,
                        meta.title,
                        meta.started_at,
                        meta.updated_at,
                        meta.turns,
                        meta.events,
                        meta.cost_usd,
                        meta.duration_seconds,
                        json.dumps(list(meta.files_touched)),
                        json.dumps(list(meta.checkpoint_ids)),
                        meta.unreadable,
                    ),
                )
                if turns and self.searchable:
                    for turn_index in {turn for turn, _role, _body in turns}:
                        conn.execute(
                            "DELETE FROM turn_text WHERE session_id = ? AND turn = ?",
                            (meta.session_id, turn_index),
                        )
                    conn.executemany(
                        "INSERT INTO turn_text (session_id, turn, role, body) VALUES (?,?,?,?)",
                        [(meta.session_id, turn, role, body) for turn, role, body in turns],
                    )
            return True
        except (OSError, sqlite3.Error) as exc:
            self._fail(
                f"the session index stopped accepting writes ({exc}) — the transcript "
                f"is unaffected and `ronin sessions --reindex` will rebuild it"
            )
            return False

    def forget(self, session_id: str) -> bool:
        """Drop one session from the index. Never raises, for :meth:`record`'s reason."""
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                if self.searchable:
                    conn.execute("DELETE FROM turn_text WHERE session_id = ?", (session_id,))
            return True
        except (OSError, sqlite3.Error) as exc:
            self._fail(f"could not remove {session_id} from the session index ({exc})")
            return False

    def rebuild(self, directory: Path) -> RebuildReport:
        """Rebuild the whole index from the transcripts in ``directory``.

        The recovery procedure for every way this file can go wrong, and the reason it
        is allowed to go wrong at all.

        Metadata comes from :func:`~ronin.persistence.transcript.list_sessions` rather
        than from the logs' header lines. That is deliberate reuse: the header is
        identity-only and its counters are from *before* the first turn, so rebuilding
        from it would write zeroed costs over rows that were previously right. The
        sidecar is the live rollup and ``list_sessions`` is the function that already
        knows to prefer it, fall back to the header, and mark the difference.

        Only the searchable text comes from the log, read with
        :func:`~ronin.persistence.transcript.read_events`, so a torn tail is tolerated
        exactly as it is everywhere else. A version mismatch is *skipped with its
        reason* and still gets its metadata row — one unreadable session must not cost
        the other nine hundred, and vanishing from the picker is the worst thing that
        could happen to it.

        Rows whose ``.jsonl`` no longer exists are removed: a deleted session that
        still appears in the picker is a worse bug than a slow picker.

        Never raises. A rebuild that cannot even prepare the database reports why and
        returns a report saying nothing was indexed — the command a user runs to fix
        the index must not be the command that ends in a traceback.
        """
        try:
            self._prepare()
        except (OSError, sqlite3.Error) as exc:
            self._fail(f"could not prepare the session index for a rebuild ({exc})")
            return RebuildReport(skipped=(f"{directory}: {exc}",))
        known = {meta.session_id: meta for meta in list_sessions(directory)}
        indexed = 0
        skipped: list[str] = []
        seen: set[str] = set()
        for path in sorted(directory.glob(f"*{SUFFIX}")):
            session_id = path.name[: -len(SUFFIX)]
            if not valid_session_id(session_id):
                skipped.append(f"{path.name}: not a valid session id")
                continue
            meta = known.get(session_id)
            if meta is None:
                skipped.append(f"{path.name}: nothing there to describe (no header, no sidecar)")
                continue
            text: tuple[tuple[int, str, str], ...] = ()
            if meta.loadable:
                try:
                    text = searchable_turns(read_events(path).events)
                except TranscriptError as exc:
                    skipped.append(f"{path.name}: listed, but not searchable ({exc})")
            else:
                skipped.append(f"{path.name}: listed, but not searchable ({meta.unreadable})")
            # Clear the session out first. :meth:`record` replaces text per *turn*, so
            # on its own it would leave behind rows for turns that no longer exist —
            # which is precisely the state a rebuild is run to fix (a log restored from
            # a backup, or truncated by hand).
            self.forget(session_id)
            if self.record(meta, text):
                indexed += 1
                seen.add(session_id)
            else:
                skipped.append(f"{path.name}: the index would not accept the write")
        removed = self._prune(seen)
        return RebuildReport(indexed=indexed, removed=removed, skipped=tuple(skipped))

    def _prune(self, keep: set[str]) -> int:
        """Drop indexed sessions whose transcript is gone. Never raises."""
        try:
            with closing(self._connect()) as conn, conn:
                rows = [str(row[0]) for row in conn.execute("SELECT session_id FROM sessions")]
                stale = [session_id for session_id in rows if session_id not in keep]
                for session_id in stale:
                    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                    if self.searchable:
                        conn.execute("DELETE FROM turn_text WHERE session_id = ?", (session_id,))
            return len(stale)
        except (OSError, sqlite3.Error) as exc:
            self._fail(f"could not prune the session index ({exc})")
            return 0

    # -------------------------------------------------------------------- reading

    def recent(self, *, limit: int = 50, cwd: str | None = None) -> tuple[SessionMeta, ...]:
        """Sessions newest first, optionally only those from one directory.

        Raises :class:`SessionIndexError` rather than returning ``()`` for a broken
        index: the caller asked a question, and an empty answer is indistinguishable
        from "you have no sessions" — which would send someone looking for their work
        in the wrong place.
        """
        sql = "SELECT * FROM sessions"
        params: list[Any] = []
        if cwd is not None:
            sql += " WHERE cwd = ?"
            params.append(cwd)
        sql += " ORDER BY updated_at DESC, session_id DESC LIMIT ?"
        params.append(max(0, limit))
        return tuple(_meta_of(row) for row in self._query(sql, params))

    def costliest(self, *, limit: int = 10) -> tuple[SessionMeta, ...]:
        """The most expensive sessions. The ``ORDER BY`` the filesystem cannot do."""
        return tuple(
            _meta_of(row)
            for row in self._query(
                "SELECT * FROM sessions ORDER BY cost_usd DESC, session_id DESC LIMIT ?",
                [max(0, limit)],
            )
        )

    def search(self, query: str, *, limit: int = 20) -> tuple[SearchHit, ...]:
        """Full-text search over recorded prompts and answers, best match first.

        ``query`` is a human's words, not fts5 syntax — :func:`fts_query` converts it.
        A query with no words at all returns ``()``: there is nothing to look for, and
        that is not an error worth raising.
        """
        expression = fts_query(query)
        if not expression:
            return ()
        if not self.searchable:
            raise SessionIndexError(
                "full-text search is unavailable because this Python's sqlite was "
                "built without the fts5 module; session metadata is still indexed, and "
                "`ronin sessions` lists sessions without it"
            )
        rows = self._query(
            """
            SELECT t.session_id AS session_id,
                   t.turn       AS turn,
                   t.role       AS role,
                   snippet(turn_text, 3, '<<', '>>', '...', 12) AS snippet,
                   COALESCE(s.cwd, '')       AS cwd,
                   COALESCE(s.updated_at, 0) AS updated_at
            FROM turn_text AS t
            LEFT JOIN sessions AS s ON s.session_id = t.session_id
            WHERE turn_text MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            [expression, max(0, limit)],
        )
        return tuple(
            SearchHit(
                session_id=str(row["session_id"]),
                turn=int(row["turn"]),
                role=str(row["role"]),
                snippet=str(row["snippet"]),
                cwd=str(row["cwd"]),
                updated_at=float(row["updated_at"]),
            )
            for row in rows
        )

    def count(self) -> int:
        """How many sessions are indexed. Cheap, and the honest health check."""
        rows = self._query("SELECT COUNT(*) AS n FROM sessions", [])
        return int(rows[0]["n"]) if rows else 0

    def _query(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        """Run a read, turning sqlite's failures into one named error."""
        try:
            with closing(self._connect()) as conn:
                return list(conn.execute(sql, tuple(params)))
        except (OSError, sqlite3.Error) as exc:
            raise SessionIndexError(
                f"the session index at {self.path} could not be read ({exc}); it is a "
                f"cache of the transcripts, so `ronin sessions --reindex` rebuilds it "
                f"and nothing is lost"
            ) from exc


def _meta_of(row: sqlite3.Row) -> SessionMeta:
    """One indexed row back as a :class:`SessionMeta`.

    The index returns the type the picker already understands, so nothing downstream
    learns that sqlite exists — which is what keeps the index optional.
    """
    return SessionMeta(
        session_id=str(row["session_id"]),
        cwd=str(row["cwd"]),
        model=str(row["model"]),
        title=str(row["title"]),
        started_at=float(row["started_at"]),
        updated_at=float(row["updated_at"]),
        turns=int(row["turns"]),
        events=int(row["events"]),
        cost_usd=float(row["cost_usd"]),
        duration_seconds=float(row["duration_seconds"]),
        files_touched=tuple(_json_list(row["files_touched"])),
        checkpoint_ids=tuple(_json_list(row["checkpoint_ids"])),
        unreadable=str(row["unreadable"]),
    )


def _json_list(value: object) -> Iterable[str]:
    """A JSON array column as strings, tolerating a row written by something else."""
    if not isinstance(value, str) or not value:
        return ()
    try:
        loaded = json.loads(value)
    except ValueError:
        return ()
    return tuple(str(item) for item in loaded) if isinstance(loaded, list) else ()


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "FTS_SCHEMA",
    "INDEX_FILENAME",
    "INDEX_SCHEMA_VERSION",
    "SCHEMA",
    "RebuildReport",
    "SearchHit",
    "SessionIndex",
    "SessionIndexError",
    "fts_query",
    "index_path",
    "searchable_turns",
]
