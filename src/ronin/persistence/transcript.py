"""The append-only session log: one JSONL file per session, plus its sidecar.

Layout under the project root::

    .ronin/sessions/<id>.jsonl       every event, one JSON object per line
    .ronin/sessions/<id>.meta.json   the rollup a session picker reads

**Why both.** The first line of the ``.jsonl`` is a header record carrying the
session's *identity* — id, cwd, model, start time — which never changes and is
therefore safe to write once, before anything can go wrong. The rollup a human
picks a session by (cost, duration, turn count, files touched, checkpoint ids)
does change, on every turn, and an append-only file cannot revise a line it
already wrote. The alternatives were to append successive metadata records and
have the picker scan for the last one, or to seek to the end of every file and
read backwards. Both make ``list_sessions`` O(bytes on disk); the sidecar makes it
O(sessions), which is the property the brief asks for. The cost is two files that
can disagree, bounded deliberately: the sidecar is a *cache*, rewritten
atomically at each turn boundary, and :func:`list_sessions` falls back to reading
**only the first line** of the ``.jsonl`` when it is missing, flagging the row
``stale`` rather than pretending the counters are current.

**Why fsync at turn boundaries and not per event.** An fsync is a device round
trip; a streaming turn emits hundreds of ``TextDelta`` events, and paying it per
event would make the transcript the slowest thing in the loop while buying
nothing — nobody resumes from the middle of a half-streamed sentence. The turn is
the unit :mod:`ronin.persistence.resume` folds and the unit a user re-issues, so
the turn boundary is exactly where durability has to be real. Between boundaries
the loss window is the current turn's tail, which replay is already built to
repair.

**Why a torn last line is not corruption.** A crash during ``write`` leaves a
partial final line. :func:`read_events` drops it, says so in
:attr:`ReadResult.skipped`, and does not raise — that case is the normal outcome
of a crash, not a broken file. A malformed line anywhere *else* is corruption and
raises, because silently skipping a record in the middle would hand replay a
transcript with a hole in it. And re-opening a torn file truncates the torn tail
before appending: left in place it would stop being the last line and start being
a mid-file record, which turns a recoverable crash into an unloadable session.

Records are written with ``ensure_ascii=True``. That is not decoration: it means a
torn write can never split a UTF-8 sequence in half (so the file always decodes),
and a lone surrogate in model output — a real product of a truncated provider
stream — is escaped instead of raising ``UnicodeEncodeError`` and losing the line
we were trying to record.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from secrets import token_hex
from typing import Any, TextIO

from ..core.types import Event, ToolEnd, TurnEnd
from .codec import (
    SCHEMA_VERSION,
    TYPE_KEY,
    VERSION_KEY,
    check_version,
    decode_event,
    encode_event,
    record_type,
)

#: Where sessions live, relative to the project root.
SESSIONS_SUBDIR = Path(".ronin") / "sessions"

#: Discriminator of the first line of every transcript.
HEADER_TAG = "session_header"

SUFFIX = ".jsonl"
META_SUFFIX = ".meta.json"


class TranscriptError(RuntimeError):
    """A transcript could not be written, or is corrupt somewhere it must not be."""


def sessions_dir(root: Path) -> Path:
    """``<root>/.ronin/sessions``. Not created — writers create it on open."""
    return root / SESSIONS_SUBDIR


def session_path(directory: Path, session_id: str) -> Path:
    return directory / f"{session_id}{SUFFIX}"


def meta_path(directory: Path, session_id: str) -> Path:
    return directory / f"{session_id}{META_SUFFIX}"


def new_session_id(
    *,
    clock: Callable[[], float] = time.time,
    entropy: Callable[[int], str] = token_hex,
) -> str:
    """A sortable, collision-resistant id: ``YYYYmmdd-HHMMSS-<6 hex>``.

    Time-ordered so a directory listing is chronological, with entropy because two
    sessions started in the same second is a thing that happens. Both sources are
    injected so a test gets a fixed id without patching the clock globally.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
    return f"{stamp}-{entropy(3)}"


# --------------------------------------------------------------------------- #
# Session metadata
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SessionMeta:
    """What a picker shows, and what an export headers itself with.

    ``cwd`` is part of the identity, not decoration: ``ronin --continue`` means
    "the most recent session *in this directory*", so a session that cannot say
    where it ran cannot be continued.

    ``stale`` marks a row whose counters came from the header line because the
    sidecar was missing — a crash before the first turn boundary. The row is still
    shown; it just does not claim the counters are current.
    """

    session_id: str
    cwd: str = "."
    model: str = ""
    title: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    turns: int = 0
    events: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    files_touched: tuple[str, ...] = ()
    checkpoint_ids: tuple[str, ...] = ()
    stale: bool = False

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("SessionMeta.session_id is required")


def encode_meta(meta: SessionMeta) -> dict[str, Any]:
    """The header line and the sidecar share one encoding — one format to read."""
    return {
        VERSION_KEY: SCHEMA_VERSION,
        TYPE_KEY: HEADER_TAG,
        "session_id": meta.session_id,
        "cwd": meta.cwd,
        "model": meta.model,
        "title": meta.title,
        "started_at": meta.started_at,
        "updated_at": meta.updated_at,
        "turns": meta.turns,
        "events": meta.events,
        "cost_usd": meta.cost_usd,
        "duration_seconds": meta.duration_seconds,
        "files_touched": list(meta.files_touched),
        "checkpoint_ids": list(meta.checkpoint_ids),
    }


def decode_meta(record: Mapping[str, Any]) -> SessionMeta:
    """Inverse of :func:`encode_meta`; version-checked like every other record."""
    check_version(record)
    if record_type(record) != HEADER_TAG:
        raise TranscriptError(
            f"expected a {HEADER_TAG!r} record, got {record.get(TYPE_KEY)!r}"
        )
    return SessionMeta(
        session_id=str(record["session_id"]),
        cwd=str(record.get("cwd", ".")),
        model=str(record.get("model", "")),
        title=str(record.get("title", "")),
        started_at=float(record.get("started_at", 0.0)),
        updated_at=float(record.get("updated_at", 0.0)),
        turns=int(record.get("turns", 0)),
        events=int(record.get("events", 0)),
        cost_usd=float(record.get("cost_usd", 0.0)),
        duration_seconds=float(record.get("duration_seconds", 0.0)),
        files_touched=tuple(str(item) for item in record.get("files_touched", ())),
        checkpoint_ids=tuple(str(item) for item in record.get("checkpoint_ids", ())),
    )


def _dumps(record: Mapping[str, Any], what: str) -> str:
    """One record as one line, or a loud failure naming what would not serialize."""
    try:
        return json.dumps(
            record, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise TranscriptError(
            f"cannot record {what}: {exc}. Tool arguments and message metadata must "
            f"be JSON values — the provider layer normalizes them before they reach "
            f"the transcript, so this is a bug upstream of persistence."
        ) from exc


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _fsync_dir(directory: Path) -> None:
    """Make the directory entry durable too.

    A file whose *name* has not reached disk can vanish entirely on power loss,
    taking a session that fsynced its contents with it. Cost is one call per open.
    Platforms that cannot fsync a directory (Windows) raise ``OSError``; there the
    content fsyncs are what we have.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform-dependent
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - platform-dependent
        pass
    finally:
        os.close(fd)


def _truncate_torn_tail(path: Path) -> str | None:
    """Drop an unterminated final line, returning a report if one was dropped."""
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return None
    cut = data.rfind(b"\n") + 1
    dropped = len(data) - cut
    os.truncate(path, cut)
    return (
        f"dropped {dropped} bytes of an unterminated final line before appending "
        f"(a crash mid-write); it would otherwise have become a mid-file record"
    )


class Transcript:
    """A writer for one session's log. Mutable because appending *is* the point.

    Holds the open handle, the running :class:`SessionMeta`, and nothing else. Use
    it as a context manager, or call :meth:`close` — both flush and fsync, so a
    clean exit never relies on the last turn having ended.
    """

    __slots__ = ("_clock", "_closed", "_handle", "_meta", "_path", "_repairs", "_seen_files")

    def __init__(
        self,
        path: Path,
        handle: TextIO,
        meta: SessionMeta,
        clock: Callable[[], float],
        repairs: tuple[str, ...] = (),
    ) -> None:
        self._path = path
        self._handle = handle
        self._meta = meta
        self._clock = clock
        self._repairs = repairs
        self._seen_files = dict.fromkeys(meta.files_touched)
        self._closed = False

    # ---------------------------------------------------------------- opening

    @classmethod
    def open(
        cls,
        directory: Path,
        session_id: str,
        *,
        model: str = "",
        cwd: str = ".",
        title: str = "",
        clock: Callable[[], float] = time.time,
    ) -> Transcript:
        """Open (or re-open) a session for appending.

        Re-opening an existing session keeps its recorded identity — a session's
        cwd and start time are facts about when it began, not about this process —
        and first truncates a torn final line so the append cannot bury it.
        """
        directory.mkdir(parents=True, exist_ok=True)
        path = session_path(directory, session_id)
        repairs: tuple[str, ...] = ()
        meta: SessionMeta | None = None

        if path.exists() and path.stat().st_size > 0:
            report = _truncate_torn_tail(path)
            if report is not None:
                repairs = (report,)
            meta = _read_meta(directory, session_id)

        fresh = meta is None
        if meta is None:
            now = clock()
            meta = SessionMeta(
                session_id=session_id,
                cwd=cwd,
                model=model,
                title=title,
                started_at=now,
                updated_at=now,
            )
        else:
            meta = replace(meta, stale=False)

        handle = path.open("a", encoding="utf-8", newline="\n")
        transcript = cls(path, handle, meta, clock, repairs)
        if fresh:
            # The header goes down before any event so identity survives a crash in
            # the first turn; the sidecar goes down with it so the picker never has
            # to open the log at all.
            handle.write(_dumps(encode_meta(meta), "the session header") + "\n")
            transcript._sync()
            _fsync_dir(directory)
        return transcript

    # ---------------------------------------------------------------- writing

    @property
    def path(self) -> Path:
        return self._path

    @property
    def meta(self) -> SessionMeta:
        """The rollup as of the last append. A value, so a caller cannot edit it."""
        return self._meta

    @property
    def repairs(self) -> tuple[str, ...]:
        """What re-opening had to fix. Empty for a clean session."""
        return self._repairs

    def append(self, event: Event) -> None:
        """Record one event. Fsyncs iff the event ends a turn.

        Every event is recorded — the whole session is replayable, which is the
        only way ``--resume`` can show what happened rather than just its result.
        """
        if self._closed:
            raise TranscriptError(f"transcript {self._path.name} is closed")
        line = _dumps(encode_event(event), f"a {type(event).__name__} event")
        self._handle.write(line + "\n")
        self._note(event)
        if isinstance(event, TurnEnd):
            self._sync()

    def extend(self, events: Iterable[Event]) -> None:
        for event in events:
            self.append(event)

    def note_files(self, paths: Iterable[str]) -> None:
        """Record files this session touched that no tool artifact named.

        ``ToolEnd`` artifacts cover the tools that report them; the orchestrator
        knows about the rest (a checkpointed file, an applied patch).
        """
        for path in paths:
            self._seen_files.setdefault(path, None)
        self._meta = replace(self._meta, files_touched=tuple(self._seen_files))

    def _note(self, event: Event) -> None:
        meta = replace(self._meta, events=self._meta.events + 1)
        if isinstance(event, ToolEnd):
            for artifact in event.result.artifacts:
                self._seen_files.setdefault(artifact, None)
            meta = replace(meta, files_touched=tuple(self._seen_files))
        elif isinstance(event, TurnEnd):
            now = self._clock()
            meta = replace(
                meta,
                turns=meta.turns + 1,
                updated_at=now,
                duration_seconds=max(0.0, now - meta.started_at),
            )
            state = event.agent_state
            if state is not None:
                checkpoints = meta.checkpoint_ids
                if state.checkpoint_id and state.checkpoint_id not in checkpoints:
                    checkpoints = (*checkpoints, state.checkpoint_id)
                meta = replace(
                    meta, cost_usd=state.budget.spent_usd, checkpoint_ids=checkpoints
                )
        self._meta = meta

    def _sync(self) -> None:
        """Flush, fsync, then rewrite the sidecar atomically. Order matters.

        The sidecar is a cache of the log: writing it *after* the log's fsync means
        a crash can leave it one turn behind, never one turn ahead of a turn that
        does not exist on disk.
        """
        self._handle.flush()
        os.fsync(self._handle.fileno())
        _write_meta(self._path.parent, self._meta)

    def close(self) -> None:
        """Flush, fsync, write the sidecar, close. Idempotent."""
        if self._closed:
            return
        try:
            self._sync()
        finally:
            self._closed = True
            self._handle.close()

    def __enter__(self) -> Transcript:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _write_meta(directory: Path, meta: SessionMeta) -> None:
    """Replace the sidecar atomically — a half-written picker row is a crash."""
    target = meta_path(directory, meta.session_id)
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = _dumps(encode_meta(meta), "the session metadata")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def _read_meta(directory: Path, session_id: str) -> SessionMeta | None:
    """The sidecar if it is readable, else the header line, else ``None``.

    Never reads past the first line of the log: this is the function that makes
    :func:`list_sessions` cheap, and it would stop being cheap the moment it
    started scanning for the truth.
    """
    sidecar = meta_path(directory, session_id)
    if sidecar.exists():
        try:
            return decode_meta(json.loads(sidecar.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TranscriptError):
            pass  # fall through to the header, which cannot have been rewritten
    path = session_path(directory, session_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
    except OSError:  # pragma: no cover - unreadable file
        return None
    if not first.strip():
        return None
    try:
        return replace(decode_meta(json.loads(first)), stale=True)
    except (ValueError, KeyError, TranscriptError):
        return None


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ReadResult:
    """Everything one transcript file yielded, including what it could not.

    ``skipped`` is not a log line — it is part of the result, because a caller that
    resumes from a crashed session needs to be able to *tell the user* the tail was
    incomplete. Silence there is how "my last message vanished" becomes a mystery.
    """

    events: tuple[Event, ...] = ()
    header: SessionMeta | None = None
    skipped: tuple[str, ...] = ()

    @property
    def truncated(self) -> bool:
        return bool(self.skipped)


def read_events(path: Path) -> ReadResult:
    """Load one transcript. Tolerant of a torn tail, intolerant of a hole.

    Raises :class:`TranscriptError` for a malformed record anywhere but the last
    line, and for a file that is not valid UTF-8. Never raises because a crash
    interrupted the final write.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TranscriptError(f"cannot read transcript {path}: {exc}") from exc
    if not raw:
        return ReadResult()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TranscriptError(
            f"transcript {path} is not valid UTF-8 ({exc}); Ronin writes pure ASCII "
            f"records, so this file was written or edited by something else"
        ) from exc

    lines = text.split("\n")
    terminated = lines[-1] == ""
    if terminated:
        lines.pop()

    events: list[Event] = []
    header: SessionMeta | None = None
    skipped: list[str] = []
    last = len(lines) - 1

    for number, line in enumerate(lines):
        is_last = number == last
        # An unterminated final line is a crash mid-write, by construction.
        partial = is_last and not terminated
        if not line.strip():
            skipped.append(f"line {number + 1}: blank")
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise TranscriptError("record is not a JSON object")
            if number == 0 and record.get(TYPE_KEY) == HEADER_TAG:
                header = decode_meta(record)
                continue
            events.append(decode_event(record))
        except (ValueError, TranscriptError) as exc:
            if partial:
                skipped.append(
                    f"line {number + 1}: incomplete final record dropped "
                    f"({len(line)} bytes, no line terminator) — the session ended "
                    f"mid-write"
                )
                continue
            if is_last:
                skipped.append(f"line {number + 1}: unreadable final record dropped ({exc})")
                continue
            raise TranscriptError(
                f"{path}:{number + 1}: malformed record in the middle of the "
                f"transcript ({exc}); refusing to load a transcript with a hole in "
                f"it — only a torn *final* line is recoverable"
            ) from exc
    return ReadResult(tuple(events), header, tuple(skipped))


def list_sessions(directory: Path) -> tuple[SessionMeta, ...]:
    """Every session in ``directory``, newest first, without reading the events.

    One sidecar read per session (or one *line* of the log when the sidecar is
    missing). A picker over a thousand sessions costs a thousand small reads, not
    a thousand full transcripts.
    """
    if not directory.is_dir():
        return ()
    found: list[SessionMeta] = []
    for path in sorted(directory.glob(f"*{SUFFIX}")):
        meta = _read_meta(directory, path.name[: -len(SUFFIX)])
        if meta is not None:
            found.append(meta)
    found.sort(key=lambda meta: (meta.updated_at, meta.session_id), reverse=True)
    return tuple(found)


__all__ = [
    "HEADER_TAG",
    "META_SUFFIX",
    "SESSIONS_SUBDIR",
    "SUFFIX",
    "ReadResult",
    "SessionMeta",
    "Transcript",
    "TranscriptError",
    "decode_meta",
    "encode_meta",
    "list_sessions",
    "meta_path",
    "new_session_id",
    "read_events",
    "session_path",
    "sessions_dir",
]
