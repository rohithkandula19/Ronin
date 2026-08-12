"""The two status-line facts the event stream cannot carry.

``ronin.ui.render.render_status`` prints the model, the context share, the spend, the
directory and the git branch, and it deliberately derives none of them: "no ``git``
subprocess for the branch, no ``os.getcwd()``". The reducer can supply spend, because the
loop reports it. The other two have to come from here.

**The branch is read, not shelled out for.** ``.git/HEAD`` is one line of plain text in a
format that has not changed in the lifetime of the format, so a subprocess buys nothing
and costs a fork per repaint plus a dependency on ``git`` being installed and on the
sandbox letting it run. A worktree's ``.git`` is a file pointing elsewhere; that is
followed. Anything unreadable or unrecognised is reported as *no branch* rather than
guessed at.

**Occupancy is not spend.** ``Budget.spent_tokens`` is cumulative for the session: every
prompt and every completion, including the ones compaction has since folded away. The
context share is a different number — how full the window is *now* — and the two diverge
immediately and permanently. Printing the first under the second's label would make the
status line lie a little more with every turn, so :func:`context_share` measures the live
transcript against the window the compaction policy is already configured with.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..context.compaction import CompactionPolicy, transcript_tokens
from ..core.types import Message

#: Prefix of a ``.git/HEAD`` that names a branch rather than a commit.
HEAD_REF_PREFIX = "ref: "

#: Prefix that means the branch lives under ``refs/heads``.
BRANCH_PREFIX = "refs/heads/"

#: Prefix of a ``.git`` *file* — a worktree or a submodule — naming the real git dir.
GITDIR_PREFIX = "gitdir: "

#: How much of a detached ``HEAD``'s commit id is shown. Enough to identify, short
#: enough to sit in a one-line status bar next to four other facts.
DETACHED_LENGTH = 10


def git_dir(root: Path) -> Path | None:
    """The git directory for ``root``, following a worktree's ``.git`` file.

    ``None`` when there is nothing to follow, which is the ordinary case for a
    directory that simply is not a repository.
    """
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if not dot_git.is_file():
        return None
    try:
        text = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith(GITDIR_PREFIX):
        return None
    pointed = Path(text[len(GITDIR_PREFIX) :].strip())
    return pointed if pointed.is_absolute() else (root / pointed)


def current_branch(root: Path) -> str:
    """The branch checked out in ``root``, or ``""`` if that cannot be established.

    A detached ``HEAD`` has no branch to name, so the short commit id is returned
    instead — a status line that said nothing there would be indistinguishable from one
    outside a repository, and those are different situations to be in.
    """
    directory = git_dir(root)
    if directory is None:
        return ""
    try:
        head = (directory / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if head.startswith(HEAD_REF_PREFIX):
        ref = head[len(HEAD_REF_PREFIX) :].strip()
        return ref[len(BRANCH_PREFIX) :] if ref.startswith(BRANCH_PREFIX) else ref
    return head[:DETACHED_LENGTH] if head else ""


def context_share(messages: Sequence[Message], policy: CompactionPolicy) -> float:
    """How full the context window is, as ``0.0`` to ``1.0``.

    Clamped at 1.0 rather than allowed past it: a transcript momentarily over the window
    is a real state (it is what triggers compaction), but "103% ctx" reads as a broken
    gauge rather than as the warning it is, and the warning is already carried by the
    number being pinned at full.
    """
    if policy.context_window <= 0:  # pragma: no cover - CompactionPolicy rejects this
        return 0.0
    return min(transcript_tokens(messages) / policy.context_window, 1.0)


__all__ = [
    "BRANCH_PREFIX",
    "DETACHED_LENGTH",
    "GITDIR_PREFIX",
    "HEAD_REF_PREFIX",
    "context_share",
    "current_branch",
    "git_dir",
]
