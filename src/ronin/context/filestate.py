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

A record also answers a second question, which is not the same one: **does the
model still have this file's content in front of it?** "Read at some point" and
"still in context" diverge the moment compaction folds the middle of a session, and
conflating them is how the guard above starts asserting something false —
``UNCHANGED`` reads "safe to edit against the content you have" to a model that no
longer has it. :meth:`FileStateTracker.retain_only` is how the session keeps the two
in step; the caller decides what "visible" means, because the transcript is not
something this layer can see.

The same record is what makes a repeat read answerable with a stub instead of the
bytes, and the honesty condition there is exact: *re-running the call would return
what the model already has*. That needs the **window** as well as the digest — a
whole-file stub after a twenty-line read would claim content the model never saw —
which is why :class:`ReadWindow` is recorded alongside the digest.

Three details that look like nitpicks and are not:

* **The digest is over bytes.** ``Path.read_text()`` applies universal-newline
  translation, so a CRLF→LF rewrite of the whole file compares equal as text and
  would slip through as "unchanged".
* **mtime is a hint, not the answer.** ``touch`` changes mtime and nothing else;
  a formatter that rewrites a file byte-for-byte identically likewise. Content is
  what the model reasoned about, so content is what decides — the stat is only a
  fast path to skip hashing.
* **The window is compared as requested, not as served.** Both calls hit the same
  line cap on the same file, so the cap cancels out; comparing what was *asked for*
  keeps the rule a two-field equality instead of a re-derivation that could drift
  from the read tool.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
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
class ReadWindow:
    """The slice of a file a read asked for. ``WHOLE_FILE`` is the default.

    Stored as *requested* rather than as served: a read of a 50,000-line file is
    capped by the read tool either way, so the cap is the same on both calls and
    cancels out of the comparison. Re-deriving the served window here would mean
    this module owning a copy of that cap, which is one more thing to drift.
    """

    #: First line, 1-based. Normalized so ``read(path)`` and ``read(path, offset=1)``
    #: are the same window rather than two that merely mean the same thing.
    offset: int = 1
    #: Maximum lines requested, or ``None`` for "as much as the tool will give".
    limit: int | None = None

    @classmethod
    def of(cls, offset: int | None = None, limit: int | None = None) -> ReadWindow:
        """Normalize a requested window.

        ``limit`` is dropped unless it is positive. ``limit=0`` is not "zero lines" to
        the read tool — it slices nothing off and serves the default — and carrying it
        through would have the stub render ``lines 1-0``.
        """
        return cls(
            offset=max(offset, 1) if offset else 1,
            limit=limit if limit is not None and limit > 0 else None,
        )


#: What a bare ``read(path)`` asks for.
WHOLE_FILE = ReadWindow()


@dataclass(frozen=True, slots=True)
class ReadRecord:
    """What one read observed. ``size``/``mtime_ns`` are the hash-skipping fast path."""

    path: str
    digest: str
    size: int
    mtime_ns: int
    #: The slice that was asked for. Two reads of one file are interchangeable only
    #: if they asked for the same lines.
    window: ReadWindow = WHOLE_FILE
    #: Whether the model actually received everything that read returned. False when
    #: the tool hit its line cap or the gate clamped the output — the record is still
    #: a valid change-detection baseline, but it can no longer stand in for the
    #: content, so a repeat read must be served rather than answered with a stub.
    complete: bool = True


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

    def record_read(
        self,
        path: Path,
        data: bytes | None = None,
        *,
        window: ReadWindow = WHOLE_FILE,
        complete: bool = True,
    ) -> ReadRecord:
        """Remember that ``path`` was read, what it contained, and which lines were asked for.

        ``data`` is accepted so a caller that already has the bytes does not pay a
        second read — and, more importantly, so the digest is of the bytes the model
        was actually shown rather than of a re-read that may already have raced.

        The digest stays over the **whole file** even for a windowed read. That is
        deliberate: it is the change-detection baseline, and a digest of twenty lines
        would call a file unchanged after an edit five hundred lines away. ``window``
        is the separate fact — what the model actually saw — and the two are kept
        apart because they answer different questions.
        """
        payload = self.read_bytes(path) if data is None else data
        stat = path.stat()
        record = ReadRecord(
            path=str(path),
            digest=digest_bytes(payload),
            size=len(payload),
            mtime_ns=stat.st_mtime_ns,
            window=window,
            complete=complete,
        )
        self._records[record.path] = record
        return record

    def mark_incomplete(self, path: Path) -> None:
        """Record that the model saw only part of what the last read returned.

        Called after the fact because truncation is decided downstream of recording:
        the digest is taken from the file and must not wait on the clamp, but whether
        the *model* got all of it is only known once the clamp has run.
        """
        record = self._records.get(str(path))
        if record is not None and record.complete:
            self._records[str(path)] = replace(record, complete=False)

    def satisfies(self, path: Path, window: ReadWindow = WHOLE_FILE) -> bool:
        """Whether re-reading ``window`` of ``path`` would return what the model has.

        The whole condition for answering a repeat read with a stub instead of the
        bytes, in one place so nothing can implement half of it: the path was read,
        the read asked for these exact lines, and the file has not moved since.

        A record the session has dropped — see :meth:`retain_only` — is not a read,
        so a file whose content compaction folded away answers ``False`` here and the
        model gets the bytes.
        """
        record = self._records.get(str(path))
        if record is None or record.window != window or not record.complete:
            return False
        return self.check(path).status is FileStatus.UNCHANGED

    def retain_only(self, visible: Iterable[str]) -> tuple[str, ...]:
        """Forget every recorded read whose content is no longer in front of the model.

        Called after a compaction with the paths whose read results survived the fold.
        What it fixes is a guard that had grown quietly false: ``check`` compares
        digests against disk and cannot see a transcript, so a file whose content was
        folded away still reported ``UNCHANGED`` — "safe to edit against the content
        you have" — to a model holding nothing of the kind.

        Forgetting is the right verb rather than a separate "not visible" flag: every
        question a caller asks of a record ("may I edit blind", "may I answer this
        read with a stub") has the same answer once the content is gone, and the one
        that does not — ``NEVER_READ`` — is already the honest one.

        Returns what it dropped, sorted, so the caller can say so.
        """
        keep = {str(path) for path in visible}
        dropped = tuple(sorted(set(self._records) - keep))
        for path in dropped:
            del self._records[path]
        return dropped

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
