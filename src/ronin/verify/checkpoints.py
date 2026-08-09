"""A shadow git repo that lets people accept edits without fear.

Checkpoints are what make an agent fast to work with: if every mutating turn is
recoverable with one command, a user stops reading each diff like a contract. So
the mechanism has to be *invisible* in normal use and *loud* when it is not
working — a checkpoint system the user does not trust is worse than none, because
they will keep reviewing every edit anyway and pay the storage cost too.

Invisible means: **the user's index is never touched.** Everything runs against a
separate git directory (``.ronin/checkpoints``) with ``--git-dir`` /
``--work-tree``, so the repository's own ``.git/index``, ``HEAD``, reflog, stash
and branch list are all untouched, and ``git status`` in the user's repo shows
exactly what it showed before. The isolated index is the shadow git-dir's own
``index`` file; nothing here ever names the real one. This is asserted in the
tests by byte-comparing ``.git/index`` across a checkpoint, not by reading the
code and hoping.

Four pieces of the ambient git environment are neutralized explicitly, because
each one is a real way a checkpoint corrupts a working tree:

* ``core.hooksPath`` — a globally configured hooks directory would run the user's
  ``pre-commit`` on every checkpoint, which is both slow and capable of editing
  files behind us.
* ``core.autocrlf`` / ``core.safecrlf`` — with ``autocrlf=true`` a restore would
  rewrite every line ending in the tree. A checkpoint must be byte-exact.
* ``commit.gpgsign`` — a signing prompt would block the session forever.
* ``user.name`` / ``user.email`` — a machine with no identity configured cannot
  commit at all, and failing a checkpoint over that is absurd.

Unavailability is a named condition, never an exception: no git binary, or a
directory that is not a git repo, degrade to :class:`Availability` with a reason a
human can act on, and the session continues without checkpoints rather than
dying at the first mutating turn.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from .runner import CommandFailure, CommandOutcome, CommandRunner, run_command

#: Where the shadow repo lives, relative to the project root.
CHECKPOINT_DIR: Final[str] = ".ronin/checkpoints"

#: Never checkpoint our own state, and never the real repo's git dir. This is
#: written into the shadow repo's ``info/exclude`` rather than relying on the
#: user's ``.gitignore``, because in a repo that does not ignore ``.ronin/`` the
#: first checkpoint would otherwise contain the second one.
SHADOW_EXCLUDES: Final[tuple[str, ...]] = ("/.ronin/", "/.git/")

_IDENTITY: Final[tuple[str, ...]] = (
    "-c",
    "user.name=ronin",
    "-c",
    "user.email=ronin@localhost",
)

_UNIT_SEP: Final[str] = "\x1f"


class CheckpointReason(StrEnum):
    """Why checkpoints are or are not working. Every value is user-actionable."""

    READY = "ready"
    NO_GIT_BINARY = "no_git_binary"
    NOT_A_REPO = "not_a_repo"
    INIT_FAILED = "init_failed"
    GIT_FAILED = "git_failed"
    NO_CHECKPOINTS = "no_checkpoints"
    UNKNOWN_CHECKPOINT = "unknown_checkpoint"


@dataclass(frozen=True, slots=True)
class Availability:
    """Whether checkpoints work here, and if not, what to tell the user."""

    available: bool
    reason: CheckpointReason
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.available and not self.detail:
            raise ValueError(
                "an unavailable checkpoint store must explain itself — a silent "
                "degradation is how a user loses work believing they were covered"
            )


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One commit in the shadow repo."""

    id: str
    label: str
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Checkpoint.id is required")

    @property
    def short(self) -> str:
        return self.id[:10]


@dataclass(frozen=True, slots=True)
class CheckpointResult:
    ok: bool
    checkpoint: Checkpoint | None = None
    reason: CheckpointReason = CheckpointReason.READY
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ok and not self.detail:
            raise ValueError("a failed CheckpointResult must say why")


@dataclass(frozen=True, slots=True)
class RestoreResult:
    ok: bool
    restored_to: str = ""
    reason: CheckpointReason = CheckpointReason.READY
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ok and not self.detail:
            raise ValueError("a failed RestoreResult must say why")


@dataclass(frozen=True, slots=True)
class DiffResult:
    ok: bool
    diff: str = ""
    base: str = ""
    reason: CheckpointReason = CheckpointReason.READY
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ok and not self.detail:
            raise ValueError("a failed DiffResult must say why")

    @property
    def empty(self) -> bool:
        return not self.diff.strip()


class CheckpointStore:
    """Checkpoints for one session, in a git repo the user never sees.

    Mutable on purpose, and only in two places: the availability probe is cached
    (it shells out, and the answer cannot change mid-session), and the *first*
    checkpoint of the session is remembered so ``/diff`` has a base. Both are
    private; every value handed out is frozen.
    """

    def __init__(
        self,
        root: Path,
        *,
        run: CommandRunner = run_command,
        git: str = "git",
        env: Mapping[str, str] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._root = root
        self._git_dir = root / CHECKPOINT_DIR
        self._run = run
        self._git = git
        self._timeout = timeout
        # When an explicit env is injected the index is pinned explicitly too, so
        # a caller who controls the environment cannot accidentally hand git the
        # ambient GIT_INDEX_FILE of an outer git command (a real hazard when ronin
        # itself is invoked from a git hook).
        self._env: dict[str, str] | None = (
            {**env, "GIT_INDEX_FILE": str(self._git_dir / "index")} if env is not None else None
        )
        self._availability: Availability | None = None
        self._session_base: str | None = None

    # ------------------------------------------------------------------ probing

    @property
    def root(self) -> Path:
        return self._root

    @property
    def git_dir(self) -> Path:
        return self._git_dir

    @property
    def session_base(self) -> str | None:
        """The first checkpoint of this session — what ``/diff`` compares against."""
        return self._session_base

    async def _git_args(self, *args: str) -> CommandOutcome:
        argv = [
            self._git,
            f"--git-dir={self._git_dir}",
            f"--work-tree={self._root}",
            "-c",
            f"core.hooksPath={self._git_dir / 'hooks'}",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.safecrlf=false",
            "-c",
            "commit.gpgsign=false",
            *_IDENTITY,
            *args,
        ]
        return await self._run(argv, cwd=self._root, timeout=self._timeout, env=self._env)

    async def ensure(self) -> Availability:
        """Create the shadow repo if needed; report a named reason if we cannot.

        A directory that is not a git repo is deliberately treated as unavailable
        rather than as "make our own repo here": without the project's own
        ``.gitignore`` and repo boundary we would happily snapshot
        ``node_modules`` and a 2GB dataset on every turn.
        """
        if self._availability is not None:
            return self._availability
        self._availability = await self._probe()
        return self._availability

    async def _probe(self) -> Availability:
        if not (self._root / ".git").exists():
            return Availability(
                available=False,
                reason=CheckpointReason.NOT_A_REPO,
                detail=(
                    f"{self._root} is not a git repository, so there is no .gitignore "
                    "to respect and no safe boundary to snapshot. run `git init` to "
                    "enable checkpoints (/undo and /diff)."
                ),
            )
        if (self._git_dir / "HEAD").is_file():
            self._write_excludes()
            return Availability(available=True, reason=CheckpointReason.READY)

        self._git_dir.parent.mkdir(parents=True, exist_ok=True)
        created = await self._run(
            [self._git, "init", "--bare", "--quiet", str(self._git_dir)],
            cwd=self._root,
            timeout=self._timeout,
            env=self._env,
        )
        if created.failure is CommandFailure.NOT_FOUND:
            return Availability(
                available=False,
                reason=CheckpointReason.NO_GIT_BINARY,
                detail=(
                    "git is not installed, so checkpoints are off: /undo and /diff "
                    "will not work this session. everything else runs normally."
                ),
            )
        if not created.ok:
            return Availability(
                available=False,
                reason=CheckpointReason.INIT_FAILED,
                detail=f"could not create {self._git_dir}: {created.output.strip()}",
            )
        # `init --bare` sets core.bare=true, and git refuses work-tree operations
        # on a bare repo even when --work-tree is passed.
        unbare = await self._run(
            [self._git, f"--git-dir={self._git_dir}", "config", "core.bare", "false"],
            cwd=self._root,
            timeout=self._timeout,
            env=self._env,
        )
        if not unbare.ok:
            return Availability(
                available=False,
                reason=CheckpointReason.INIT_FAILED,
                detail=f"could not configure {self._git_dir}: {unbare.output.strip()}",
            )
        self._write_excludes()
        return Availability(available=True, reason=CheckpointReason.READY)

    def _write_excludes(self) -> None:
        info = self._git_dir / "info"
        try:
            info.mkdir(parents=True, exist_ok=True)
            (info / "exclude").write_text("\n".join(SHADOW_EXCLUDES) + "\n", encoding="utf-8")
        except OSError:
            # Not fatal: the user's own .gitignore usually covers .ronin/, and a
            # checkpoint that includes it is wasteful rather than wrong.
            pass

    # -------------------------------------------------------------- checkpoints

    async def checkpoint(self, label: str) -> CheckpointResult:
        """Snapshot the work tree. Call this *before* a mutating turn, not after.

        Before, because the point of a checkpoint is the state you want back — the
        one that existed while the user was still happy. A checkpoint taken after
        the edit preserves the damage.
        """
        availability = await self.ensure()
        if not availability.available:
            return CheckpointResult(
                ok=False, reason=availability.reason, detail=availability.detail
            )

        staged = await self._git_args("add", "-A", "--", ".")
        if not staged.ok:
            return CheckpointResult(
                ok=False,
                reason=CheckpointReason.GIT_FAILED,
                detail=f"git add failed: {staged.output.strip()}",
            )
        committed = await self._git_args(
            "commit", "--allow-empty", "--quiet", "-m", label or "checkpoint"
        )
        if not committed.ok:
            return CheckpointResult(
                ok=False,
                reason=CheckpointReason.GIT_FAILED,
                detail=f"git commit failed: {committed.output.strip()}",
            )
        head = await self._git_args("rev-parse", "HEAD")
        if not head.ok:
            return CheckpointResult(
                ok=False,
                reason=CheckpointReason.GIT_FAILED,
                detail=f"git rev-parse failed: {head.output.strip()}",
            )
        stamp = await self._git_args("log", "-1", "--format=%cI")
        checkpoint = Checkpoint(
            id=head.output.strip(),
            label=label or "checkpoint",
            created_at=stamp.output.strip() if stamp.ok else "",
        )
        if self._session_base is None:
            self._session_base = checkpoint.id
        return CheckpointResult(ok=True, checkpoint=checkpoint)

    async def history(self) -> tuple[Checkpoint, ...]:
        """Newest first. Empty when the store is unavailable or has no commits."""
        availability = await self.ensure()
        if not availability.available:
            return ()
        listed = await self._git_args("log", f"--format=%H{_UNIT_SEP}%s{_UNIT_SEP}%cI")
        if not listed.ok:
            return ()
        checkpoints: list[Checkpoint] = []
        for line in listed.output.splitlines():
            parts = line.split(_UNIT_SEP)
            if len(parts) == 3 and parts[0]:
                checkpoints.append(
                    Checkpoint(id=parts[0], label=parts[1], created_at=parts[2])
                )
        return tuple(checkpoints)

    async def restore(self, checkpoint_id: str) -> RestoreResult:
        """``/undo``: put the work tree back exactly as the checkpoint had it.

        ``reset --hard`` restores tracked content and ``clean -fd`` removes files
        created since — without ``-x``, so ignored files (``.venv``, build caches,
        and our own ``.ronin/``) survive. Deleting those would turn an undo into a
        twenty-minute reinstall, which is not what the user asked for.
        """
        availability = await self.ensure()
        if not availability.available:
            return RestoreResult(
                ok=False, reason=availability.reason, detail=availability.detail
            )
        if not checkpoint_id:
            return RestoreResult(
                ok=False,
                reason=CheckpointReason.UNKNOWN_CHECKPOINT,
                detail="no checkpoint id given",
            )
        verified = await self._git_args("rev-parse", "--verify", f"{checkpoint_id}^{{commit}}")
        if not verified.ok:
            return RestoreResult(
                ok=False,
                reason=CheckpointReason.UNKNOWN_CHECKPOINT,
                detail=f"{checkpoint_id} is not a checkpoint in {self._git_dir}",
            )
        resolved = verified.output.strip()
        reset = await self._git_args("reset", "--hard", "--quiet", resolved)
        if not reset.ok:
            return RestoreResult(
                ok=False,
                reason=CheckpointReason.GIT_FAILED,
                detail=f"git reset failed: {reset.output.strip()}",
            )
        cleaned = await self._git_args("clean", "-fdq")
        if not cleaned.ok:
            return RestoreResult(
                ok=False,
                reason=CheckpointReason.GIT_FAILED,
                detail=(
                    f"restored tracked files to {resolved[:10]} but could not remove "
                    f"files created since: {cleaned.output.strip()}"
                ),
            )
        return RestoreResult(ok=True, restored_to=resolved)

    async def session_diff(self, *, base: str | None = None) -> DiffResult:
        """``/diff``: everything this session changed, as one patch.

        Staging into the *shadow* index first is what makes new files show up —
        ``git diff <commit>`` alone ignores untracked files, and "the diff did not
        mention the file you created" is the exact bug that makes a user stop
        trusting ``/diff``. The user's index is still not involved.
        """
        availability = await self.ensure()
        if not availability.available:
            return DiffResult(ok=False, reason=availability.reason, detail=availability.detail)
        anchor = base or self._session_base
        if anchor is None:
            return DiffResult(
                ok=False,
                reason=CheckpointReason.NO_CHECKPOINTS,
                detail=(
                    "no checkpoint has been made this session, so there is no base "
                    "to diff against"
                ),
            )
        staged = await self._git_args("add", "-A", "--", ".")
        if not staged.ok:
            return DiffResult(
                ok=False,
                reason=CheckpointReason.GIT_FAILED,
                detail=f"git add failed: {staged.output.strip()}",
            )
        diffed = await self._git_args("diff", "--cached", anchor)
        if not diffed.ok:
            return DiffResult(
                ok=False,
                reason=CheckpointReason.GIT_FAILED,
                detail=f"git diff failed: {diffed.output.strip()}",
            )
        return DiffResult(ok=True, diff=diffed.output, base=anchor)


def unavailable_note(availability: Availability) -> str:
    """One sentence for the transcript when checkpoints are off."""
    if availability.available:
        return ""
    return f"checkpoints unavailable ({availability.reason.value}): {availability.detail}"


def changed_paths_from_diff(diff: str) -> tuple[str, ...]:
    """Paths touched by a unified diff, in order, deduplicated.

    Reads the ``+++ b/<path>`` side so a rename reports its destination, and skips
    ``/dev/null`` so a deletion does not become a verify target that cannot exist.
    """
    seen: dict[str, None] = {}
    for line in diff.splitlines():
        if not line.startswith("+++ "):
            continue
        target = line[4:].strip()
        if target == "/dev/null":
            continue
        if target.startswith(("b/", "a/")):
            target = target[2:]
        if target:
            seen.setdefault(target, None)
    return tuple(seen)


__all__ = [
    "CHECKPOINT_DIR",
    "SHADOW_EXCLUDES",
    "Availability",
    "Checkpoint",
    "CheckpointReason",
    "CheckpointResult",
    "CheckpointStore",
    "DiffResult",
    "RestoreResult",
    "changed_paths_from_diff",
    "unavailable_note",
]
