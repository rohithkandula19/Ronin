"""What the model has seen, and whether it is still true.

The failure this prevents is concrete: the model reads ``config.py``, thinks for
four turns, the user fixes a typo in their editor, and the model's edit — computed
against the version it read — silently reverts the human's change. Nothing in the
provider layer can catch that, because the provider has no idea a file exists.

So every read is recorded with a digest, and every edit re-stats first. Four
outcomes, four messages, each written for a model to act on rather than for a log:

* ``UNCHANGED`` — proceed.
* ``CHANGED`` — re-read before editing; the content you are holding is stale.
* ``DELETED`` — the file is gone; do not recreate it from memory.
* ``NEVER_READ`` — read it first; an edit computed from a guess is a guess.

Two details that look like nitpicks and are not:

* **The digest is over bytes.** ``Path.read_text()`` applies universal-newline
  translation, so a CRLF→LF rewrite of the whole file compares equal as text and
  would slip through as "unchanged".
* **mtime is a hint, not the answer.** ``touch`` changes mtime and nothing else;
  a formatter that rewrites a file byte-for-byte identically likewise. Content is
  what the model reasoned about, so content is what decides — the stat is only a
  fast path to skip hashing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

#: Injected so a test never has to touch a real file to simulate a race, and so a
#: caller with an overlay filesystem can hand one in. Raises ``OSError`` for a
#: missing path, exactly like ``Path.read_bytes``.
BytesReader = Callable[[Path], bytes]


class FileStatus(StrEnum):
    """The four possible answers to "is what the model read still what is there?"."""

    UNCHANGED = "unchanged"
    CHANGED = "changed"
    DELETED = "deleted"
    NEVER_READ = "never_read"


_MESSAGES: dict[FileStatus, str] = {
    FileStatus.UNCHANGED: (
        "{path} is unchanged since you read it. Safe to edit against the content you have."
    ),
    FileStatus.CHANGED: (
        "{path} changed on disk since you read it (the user or another process edited "
        "it). Do NOT edit from the copy you are holding — read {path} again first, "
        "then apply your change to the new content."
    ),
    FileStatus.DELETED: (
        "{path} no longer exists (it was deleted or moved since you read it). Do not "
        "recreate it from memory; check where it went, or ask before writing it back."
    ),
    FileStatus.NEVER_READ: (
        "{path} has not been read in this session. Read it first — an edit computed "
        "without seeing the current content is a guess."
    ),
}


@dataclass(frozen=True, slots=True)
class FileCheck:
    """One verdict about one path, with the message to hand the model verbatim."""

    path: str
    status: FileStatus
    message: str
    #: The digest recorded at read time, or ``""`` if the file was never read.
    recorded_digest: str = ""
    #: The digest on disk right now, or ``""`` if the file is gone.
    current_digest: str = ""

    @property
    def safe_to_edit(self) -> bool:
        """Only an unchanged, previously-read file may be edited blind."""
        return self.status is FileStatus.UNCHANGED

    @property
    def must_reread(self) -> bool:
        return self.status in (FileStatus.CHANGED, FileStatus.NEVER_READ)


@dataclass(frozen=True, slots=True)
class ReadRecord:
    """What one read observed. ``size``/``mtime_ns`` are the hash-skipping fast path."""

    path: str
    digest: str
    size: int
    mtime_ns: int


def digest_bytes(data: bytes) -> str:
    """sha256 over raw bytes. The only hash function in this module, on purpose."""
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class FileStateTracker:
    """Session-scoped record of every file read, and the guard before every edit.

    Mutable because the thing being modelled is mutable: "what has been read this
    session" accumulates, and a frozen value would force the caller to thread a new
    tracker through every tool call — which is exactly how a read gets dropped and
    the guard silently stops guarding.
    """

    read_bytes: BytesReader = field(default=Path.read_bytes)
    _records: dict[str, ReadRecord] = field(default_factory=dict)

    def record_read(self, path: Path, data: bytes | None = None) -> ReadRecord:
        """Remember that ``path`` was read, and what it contained.

        ``data`` is accepted so a caller that already has the bytes does not pay a
        second read — and, more importantly, so the digest is of the bytes the model
        was actually shown rather than of a re-read that may already have raced.
        """
        payload = self.read_bytes(path) if data is None else data
        stat = path.stat()
        record = ReadRecord(
            path=str(path),
            digest=digest_bytes(payload),
            size=len(payload),
            mtime_ns=stat.st_mtime_ns,
        )
        self._records[record.path] = record
        return record

    def forget(self, path: Path) -> None:
        """Drop a record — for a file the session deliberately replaced wholesale."""
        self._records.pop(str(path), None)

    def recorded(self, path: Path) -> ReadRecord | None:
        return self._records.get(str(path))

    def known_paths(self) -> tuple[str, ...]:
        """Every path read this session, sorted so output is reproducible."""
        return tuple(sorted(self._records))

    def check(self, path: Path) -> FileCheck:
        """Re-stat and, if needed, re-hash ``path``. Call before every edit."""
        key = str(path)
        record = self._records.get(key)
        if record is None:
            return self._verdict(key, FileStatus.NEVER_READ)
        try:
            stat = path.stat()
        except OSError:
            return self._verdict(key, FileStatus.DELETED, recorded=record.digest)
        if stat.st_mtime_ns == record.mtime_ns and stat.st_size == record.size:
            return self._verdict(
                key,
                FileStatus.UNCHANGED,
                recorded=record.digest,
                current=record.digest,
            )
        try:
            current = digest_bytes(self.read_bytes(path))
        except OSError:
            return self._verdict(key, FileStatus.DELETED, recorded=record.digest)
        status = FileStatus.UNCHANGED if current == record.digest else FileStatus.CHANGED
        return self._verdict(key, status, recorded=record.digest, current=current)

    def check_all(self, paths: Iterable[Path] | None = None) -> tuple[FileCheck, ...]:
        """Verdicts for ``paths``, or for everything read so far. Sorted by path."""
        targets = sorted(str(p) for p in paths) if paths is not None else self.known_paths()
        return tuple(self.check(Path(p)) for p in targets)

    def changed_since_read(self) -> tuple[str, ...]:
        """Paths whose content moved under us — the set worth showing a user."""
        return tuple(
            check.path
            for check in self.check_all()
            if check.status in (FileStatus.CHANGED, FileStatus.DELETED)
        )

    def _verdict(
        self,
        path: str,
        status: FileStatus,
        *,
        recorded: str = "",
        current: str = "",
    ) -> FileCheck:
        return FileCheck(
            path=path,
            status=status,
            message=_MESSAGES[status].format(path=path),
            recorded_digest=recorded,
            current_digest=current,
        )
