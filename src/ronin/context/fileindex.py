"""A cached list of the repo's files, cheap enough to consult on every keystroke.

``walk_repo`` is the right answer to "what files are in this repo" and the wrong thing
to call from a keystroke handler: on this repo it is 3024 files and ~180ms, and
``@compaction`` is eight keystrokes. Called per keystroke that is a visibly broken
input line; called once and never again it stops finding files the session itself
creates. So it is called once and re-called when the answer is stale.

The staleness rule is a clock, not a filesystem watch. A watch means an OS-specific
dependency and a background thread to make a *completion list* marginally fresher,
which is a bad trade; a few seconds of lag on a file that was created seconds ago is
not a bug anyone can feel, and the file is still reachable by typing its path. The
clock is injected for the same reason every other clock in this codebase is: so a test
asserts what happens after ten seconds without waiting ten seconds.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .repomap import walk_repo

#: How long a snapshot is trusted. Long enough that a burst of typing walks the tree
#: once, short enough that a file you just created is offered by the time you have
#: finished describing what to do with it.
DEFAULT_TTL_SECONDS = 5.0

__all__ = ["DEFAULT_TTL_SECONDS", "FileIndex"]


@dataclass(slots=True)
class FileIndex:
    """Repo-relative posix paths, refreshed no more often than ``ttl_seconds``.

    Mutable because it is a cache, and lazily filled because a session that never types
    a mention must not pay for a tree walk. The first call is the slow one.

    Paths come out repo-relative and posix-formatted — the shape that is short enough
    to display in a completion list, and the shape a tool call wants — rather than the
    absolute paths ``walk_repo`` returns.
    """

    root: Path
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    clock: Callable[[], float] = time.monotonic
    walk: Callable[[Path], tuple[Path, ...]] = walk_repo
    _paths: tuple[str, ...] = field(default=(), repr=False)
    _taken_at: float | None = field(default=None, repr=False)

    def paths(self) -> tuple[str, ...]:
        """The current snapshot, walking the tree only when the last one is stale."""
        now = self.clock()
        if self._taken_at is not None and now - self._taken_at < self.ttl_seconds:
            return self._paths
        self._paths = self._walk()
        self._taken_at = now
        return self._paths

    def invalidate(self) -> None:
        """Drop the snapshot so the next read walks again.

        For the caller that *knows* the tree moved — a rewind restoring a checkpoint,
        say — and should not wait out the clock.
        """
        self._taken_at = None

    def _walk(self) -> tuple[str, ...]:
        """Walk and relativise, surviving a tree that moves under us.

        A completion list is never worth an exception reaching the input line: a path
        that cannot be made relative to the root (a symlinked walk escaping it, a race
        with a delete) is dropped rather than raised, and a walk that fails entirely
        leaves the previous snapshot in place.
        """
        try:
            found = self.walk(self.root)
        except OSError:
            return self._paths
        out: list[str] = []
        for path in found:
            try:
                out.append(path.relative_to(self.root).as_posix())
            except ValueError:
                continue
        return tuple(out)
