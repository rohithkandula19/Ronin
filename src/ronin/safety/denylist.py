"""Actions no approval can authorize. The floor under the whole gate.

Everything else in this package is negotiable: a rule can say allow, a human can say
yes, a project can widen its own settings. This module is the short list of things that
stay refused anyway, because for each of them **the approval prompt arrives too late to
help**. A human who is asked "run ``rm -rf /``?" and clicks yes by reflex has already
lost the machine; there is no undo to offer afterwards. So these are refused, and the
only way past is ``--yolo``, which is a decision a person makes once, loudly, about the
whole session — not a decision they make while tired, one prompt at a time.

Every entry answers two questions, and the refusal text carries both:

* **Why it is unconditional** — what makes this different from a merely dangerous
  action that a prompt can handle.
* **What to do instead** — because a refusal that only says "denied" teaches the model
  nothing, and a model that learns nothing retries the same command with a different
  quote style. Half the entries here exist because the safe alternative is genuinely
  easy, and saying so is what makes the refusal land.

This is a superset of ``ronin.tools.shell.DENIED_PATTERNS``: every shape that module
refuses textually is refused here structurally (root delete, ``mkfs``, ``dd`` to a
block device, a fork bomb, world-writable root, a write to a block device), so the two
cannot disagree. The difference is that this one is driven by
:func:`ronin.safety.command.parse_command`, so ``echo ok; \\rm -rf /`` is caught and
``echo "rm -rf /"`` is not.

Two scope decisions worth stating rather than burying:

* **``.env`` is denied for writes, not reads.** Overwriting a project's ``.env``
  destroys credentials that may exist nowhere else; reading one is what every dev tool
  in the world does, and denying it would make the gate expensive for no safety.
  Private key material (``.ssh``, ``.gnupg``, ``*_rsa``) *is* denied for reads too,
  because reading it is itself the first half of an exfiltration.
* **"Outside the workspace" allows a scratch root.** ``/tmp`` is where builds, logs and
  temp files legitimately go. Denying it would push every real workflow into asking for
  a waiver, and a waiver people always grant protects nothing.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .command import (
    CODE_INTERPRETERS,
    EVAL_BINARIES,
    FETCH_BINARIES,
    SHELL_EXECUTORS,
    Segment,
    parse_command,
)


class DenyCode(StrEnum):
    """One code per unconditional refusal. Stable enough to test against by name."""

    RM_ROOT = "rm_root"
    RM_HOME = "rm_home"
    DD_BLOCK_DEVICE = "dd_block_device"
    MKFS = "mkfs"
    FETCH_INTO_SHELL = "fetch_into_shell"
    CHMOD_777_RECURSIVE = "chmod_777_recursive"
    PUSH_FORCE_PROTECTED = "push_force_protected"
    RESET_HARD_NO_CHECKPOINT = "reset_hard_no_checkpoint"
    SECRET_WRITE = "secret_write"
    KEY_MATERIAL_READ = "key_material_read"
    OUTSIDE_WORKSPACE = "outside_workspace"
    FORK_BOMB = "fork_bomb"


@dataclass(frozen=True, slots=True)
class DenyReason:
    """The static half of a refusal: why it is absolute, and the way round it."""

    why: str
    alternative: str


#: The list, with its justifications. Keeping the prose here rather than at the call
#: site means the message a model sees is the same wherever the check fires.
DENY_REASONS: Mapping[DenyCode, DenyReason] = {
    DenyCode.RM_ROOT: DenyReason(
        why=(
            "a recursive delete of / destroys the operating system as well as the "
            "work, and it cannot be interrupted usefully once started"
        ),
        alternative=(
            "delete the specific directory you mean, by its path, relative to the "
            "workspace — e.g. `rm -rf build/` from the repo root"
        ),
    ),
    DenyCode.RM_HOME: DenyReason(
        why=(
            "the home directory holds ssh keys, cloud credentials, shell history and "
            "every other project; nothing in a coding task ever needs to remove it"
        ),
        alternative="name the directory inside the workspace that you actually want gone",
    ),
    DenyCode.DD_BLOCK_DEVICE: DenyReason(
        why=(
            "dd writes past the filesystem straight onto the disk, so there is no "
            "file to restore and no journal to replay"
        ),
        alternative=(
            "write to a regular file (`of=./image.img`); if a real device must be "
            "written, that is a job for the user at their own terminal"
        ),
    ),
    DenyCode.MKFS: DenyReason(
        why="creating a filesystem discards every byte already on the target",
        alternative=(
            "if you need a scratch filesystem, create a file-backed image and work in "
            "that; ask the user to format real hardware themselves"
        ),
    ),
    DenyCode.FETCH_INTO_SHELL: DenyReason(
        why=(
            "piping a download into a shell executes code that nobody — not the user, "
            "not you — has read, and the server can serve different code to each fetch"
        ),
        alternative=(
            "download to a file, read it, then run it: "
            "`curl -fsSL URL -o /tmp/install.sh` then read /tmp/install.sh, then "
            "`bash /tmp/install.sh`"
        ),
    ),
    DenyCode.CHMOD_777_RECURSIVE: DenyReason(
        why=(
            "recursively making a tree world-writable is a permanent, silent security "
            "hole, and it also erases the executable bits that were meaningful"
        ),
        alternative=(
            "fix the one path that is actually wrong, with the narrowest mode that "
            "works — usually `chmod +x path` or `chmod 644 path`"
        ),
    ),
    DenyCode.PUSH_FORCE_PROTECTED: DenyReason(
        why=(
            "a force push to the default branch rewrites history other people have "
            "already pulled; their clones break and the overwritten commits are only "
            "recoverable from someone else's reflog"
        ),
        alternative=(
            "push to a branch and open a pull request, or if history really must "
            "change, use `--force-with-lease` on a branch that is yours"
        ),
    ),
    DenyCode.RESET_HARD_NO_CHECKPOINT: DenyReason(
        why=(
            "`reset --hard` discards uncommitted work with no reflog entry to recover "
            "it from, and there is no checkpoint in this session to restore from either"
        ),
        alternative=(
            "commit or stash first (`git stash -u`), or take a checkpoint, and then "
            "reset — with either in place the same command is recoverable"
        ),
    ),
    DenyCode.SECRET_WRITE: DenyReason(
        why=(
            "these files hold credentials that frequently exist nowhere else, so "
            "overwriting or moving one locks the user out of their own accounts and "
            "machines, and copying one somewhere else is how it leaks"
        ),
        alternative=(
            "write to a new file and tell the user what to merge, or ask them to make "
            "the change themselves"
        ),
    ),
    DenyCode.KEY_MATERIAL_READ: DenyReason(
        why=(
            "reading a private key is the first half of exfiltrating it, and no "
            "coding task needs the bytes — only the public half is ever shareable"
        ),
        alternative=(
            "use the public key (`*.pub`), or ask the user to run the command that "
            "needs the key themselves"
        ),
    ),
    DenyCode.OUTSIDE_WORKSPACE: DenyReason(
        why=(
            "a write outside the workspace is invisible to git, so it cannot be "
            "reviewed in a diff, reverted with a checkout, or seen by the user at all"
        ),
        alternative=(
            "keep writes inside the workspace, or under /tmp if the file is genuinely "
            "temporary"
        ),
    ),
    DenyCode.FORK_BOMB: DenyReason(
        why="it exhausts the process table, which takes the whole machine down with it",
        alternative="if you are load-testing, bound the concurrency and target one process",
    ),
}


@dataclass(frozen=True, slots=True)
class DenyHit:
    """One unconditional refusal, bound to the text that triggered it."""

    code: DenyCode
    subject: str
    detail: str = ""

    @property
    def reason(self) -> DenyReason:
        return DENY_REASONS[self.code]

    @property
    def message(self) -> str:
        """What the model is told. Refusal, cause, and the way forward, in that order."""
        head = f"refused ({self.code.value}): `{self.subject}`"
        if self.detail:
            head = f"{head} — {self.detail}"
        return (
            f"{head}.\nThis is refused unconditionally, not gated: {self.reason.why}.\n"
            f"Do this instead: {self.reason.alternative}."
        )


# --------------------------------------------------------------------------- #
# Patterns and path classes
# --------------------------------------------------------------------------- #

#: Real block devices, as opposed to /dev/null and friends.
BLOCK_DEVICE = re.compile(
    r"^/dev/(sd[a-z]|nvme\d+n\d+|disk\d+|vd[a-z]|hd[a-z]|mmcblk\d+|loop\d+|xvd[a-z]|md\d+)"
)

#: The fork bomb, matched textually. A function definition is the one shape where the
#: parser genuinely has nothing useful to say, and ``shell.py`` already carries this
#: exact pattern — keeping it identical is how the two modules stay consistent.
FORK_BOMB = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")

#: Directory names whose whole contents are credentials.
SECRET_DIRECTORIES: frozenset[str] = frozenset({".ssh", ".aws", ".gnupg", ".gcloud"})

#: Filename suffixes that mean "this is a private key".
KEY_SUFFIXES: tuple[str, ...] = ("_rsa", "_dsa", "_ed25519", "_ecdsa", ".ppk")

#: Programs that create, truncate, move or change files. Path checks apply to these
#: and to redirect targets; a read-only program's arguments are not write candidates.
WRITE_BINARIES: frozenset[str] = frozenset(
    {
        "rm",
        "rmdir",
        "mv",
        "cp",
        "dd",
        "tee",
        "truncate",
        "shred",
        "chmod",
        "chown",
        "chgrp",
        "ln",
        "install",
        "touch",
        "mkdir",
        "unzip",
        "tar",
        "rsync",
        "sed",
        "patch",
    }
)

#: Directories outside the workspace that a build may legitimately write to.
DEFAULT_SCRATCH_ROOTS: tuple[str, ...] = ("/tmp", "/var/tmp", "/private/tmp", "/var/folders")


def _no_checkpoint() -> bool:
    """The honest default: assume nothing has been checkpointed.

    Injected rather than discovered — this module does not open a git repo, both
    because ``safety`` may not import the tool layer and because a filesystem probe
    inside a policy check is a hidden I/O dependency that makes tests need a repo.
    """
    return False


@dataclass(frozen=True, slots=True)
class Denylist:
    """The unconditional floor, parameterised by the one session it protects.

    ``has_checkpoint`` is injected because "``git reset --hard`` without a checkpoint"
    is a question about session state, not about the command text: the same command is
    reasonable the moment there is something to restore from.
    """

    workspace_root: Path
    home: Path
    has_checkpoint: Callable[[], bool] = _no_checkpoint
    protected_branches: frozenset[str] = frozenset({"main", "master", "trunk"})
    scratch_roots: tuple[Path, ...] = field(
        default_factory=lambda: tuple(Path(p) for p in DEFAULT_SCRATCH_ROOTS)
    )
    yolo: bool = False

    # -- entry points ------------------------------------------------------- #

    def check_command(self, raw: str) -> tuple[DenyHit, ...]:
        """Every unconditional refusal in ``raw``, or an empty tuple."""
        if self.yolo:
            return ()
        return (*self.check_command_text(raw), *self.check_segments(parse_command(raw)))

    def check_command_text(self, raw: str) -> tuple[DenyHit, ...]:
        """The refusals that can only be seen in the raw text, not in the parse.

        Exactly one so far. A shell function definition is the single shape where the
        parser has nothing useful to say, and ``shell.py`` already matches it textually
        — keeping the pattern identical is how the two modules stay consistent.
        """
        if self.yolo or not FORK_BOMB.search(raw):
            return ()
        return (DenyHit(code=DenyCode.FORK_BOMB, subject=raw.strip()),)

    def check_segments(self, segments: Sequence[Segment]) -> tuple[DenyHit, ...]:
        """The same check over an already-parsed command."""
        if self.yolo:
            return ()
        hits: list[DenyHit] = []
        cwd = self.workspace_root
        for index, segment in enumerate(segments):
            if segment.depth == 0 and segment.binary == "cd":
                cwd = self._retarget(cwd, segment)
            hits.extend(self._segment_hits(segments, index, segment, cwd))
        return tuple(hits)

    def check_path(self, path: str | Path, *, write: bool) -> tuple[DenyHit, ...]:
        """Refusals for touching one path, as a file tool would.

        ``write=False`` still refuses private key material: reading it is the harm.
        """
        if self.yolo:
            return ()
        return tuple(self._path_hits(str(path), self.workspace_root, write=write))

    @property
    def waived(self) -> bool:
        """Whether this denylist is refusing nothing. Never true by accident."""
        return self.yolo

    # -- per-segment checks -------------------------------------------------- #

    def _segment_hits(
        self, segments: Sequence[Segment], index: int, segment: Segment, cwd: Path
    ) -> Iterator[DenyHit]:
        binary = segment.binary
        if binary == "rm":
            yield from self._rm_hits(segment)
        if binary == "dd":
            yield from self._dd_hits(segment)
        if binary.startswith("mkfs") or binary in {"mke2fs", "newfs"}:
            yield DenyHit(code=DenyCode.MKFS, subject=segment.raw)
        if binary == "chmod":
            yield from self._chmod_hits(segment)
        if binary == "git":
            yield from self._git_hits(segment)
        yield from self._fetch_into_shell_hits(segments, index, segment)
        for redirect in segment.redirects:
            if redirect.writes and BLOCK_DEVICE.match(redirect.target):
                yield DenyHit(
                    code=DenyCode.DD_BLOCK_DEVICE,
                    subject=segment.raw,
                    detail=f"{redirect.target} is a block device",
                )
        writes = self._writes(segment)
        seen: set[tuple[DenyCode, str]] = set()
        for word in segment.path_words:
            targeted = writes or any(r.names_a_file and r.target == word for r in segment.redirects)
            for hit in self._path_hits(word, cwd, write=targeted, subject=segment.raw):
                key = (hit.code, word)
                if key not in seen:
                    seen.add(key)
                    yield hit

    def _writes(self, segment: Segment) -> bool:
        """Whether this segment's path arguments are write candidates.

        ``sed FILE`` prints; ``sed -i FILE`` rewrites. Treating the two the same would
        deny ``sed -n '1,5p' /etc/hosts``, which is a read and nobody's problem.
        """
        if segment.binary in {"sed", "perl", "ruby", "awk"}:
            return segment.has_flag("-i", "--in-place")
        return segment.binary in WRITE_BINARIES

    def _rm_hits(self, segment: Segment) -> Iterator[DenyHit]:
        if not segment.has_flag("-r", "-R", "--recursive"):
            return
        for word in segment.operands:
            kind = self._delete_class(word, segment)
            if kind is DenyCode.RM_ROOT or kind is DenyCode.RM_HOME:
                yield DenyHit(code=kind, subject=segment.raw, detail=f"target `{word}`")

    def _delete_class(self, word: str, segment: Segment) -> DenyCode | None:
        expanded = self.expand(word)
        # `rm -rf /*` and `rm -rf ~/*` are the same act as deleting the parent.
        for glob_suffix in ("/*", "/.*", "/**"):
            if expanded.endswith(glob_suffix):
                expanded = expanded[: -len(glob_suffix)] or "/"
        if expanded in {"*", "**"}:
            return None
        if not expanded.startswith("/"):
            return None
        resolved = PurePosixPath(os.path.normpath(expanded))
        # `normpath("//")` is `"//"` on POSIX — a path made only of separators is still
        # the root, and `rm -rf //` is a real way people have destroyed a machine.
        if set(str(resolved)) == {"/"}:
            return DenyCode.RM_ROOT
        if resolved == PurePosixPath(self.home):
            return DenyCode.RM_HOME
        return None

    def _dd_hits(self, segment: Segment) -> Iterator[DenyHit]:
        for word in segment.argv[1:]:
            if word.startswith("of=") and BLOCK_DEVICE.match(word[3:]):
                yield DenyHit(
                    code=DenyCode.DD_BLOCK_DEVICE,
                    subject=segment.raw,
                    detail=f"{word[3:]} is a block device",
                )

    def _chmod_hits(self, segment: Segment) -> Iterator[DenyHit]:
        if not segment.has_flag("-R", "-r", "--recursive"):
            return
        for word in segment.operands:
            if word == "777" or word in {"a+rwx", "ugo+rwx", "0777"}:
                yield DenyHit(
                    code=DenyCode.CHMOD_777_RECURSIVE,
                    subject=segment.raw,
                    detail=f"mode `{word}` applied recursively",
                )
                return

    def _git_hits(self, segment: Segment) -> Iterator[DenyHit]:
        operands = segment.operands
        if not operands:
            return
        subcommand = operands[0]
        if subcommand == "push":
            yield from self._push_hits(segment, operands[1:])
        elif subcommand == "reset" and segment.has_flag("--hard") and not self.has_checkpoint():
            yield DenyHit(
                code=DenyCode.RESET_HARD_NO_CHECKPOINT,
                subject=segment.raw,
                detail="no checkpoint exists in this session",
            )

    def _push_hits(self, segment: Segment, rest: Sequence[str]) -> Iterator[DenyHit]:
        refspecs = tuple(rest[1:]) if len(rest) > 1 else ()
        forced = segment.has_flag("-f", "--force") or any(s.startswith("+") for s in refspecs)
        if not forced:
            # `--force-with-lease` refuses rather than clobbers when the remote moved,
            # so it is a gated action, not an unconditional one.
            return
        if not refspecs:
            yield DenyHit(
                code=DenyCode.PUSH_FORCE_PROTECTED,
                subject=segment.raw,
                detail=(
                    "no refspec, so the target is whatever branch is checked out and "
                    "may be the default branch — name the branch explicitly"
                ),
            )
            return
        for spec in refspecs:
            destination = spec.lstrip("+").split(":")[-1]
            if destination in self.protected_branches or destination == "HEAD":
                yield DenyHit(
                    code=DenyCode.PUSH_FORCE_PROTECTED,
                    subject=segment.raw,
                    detail=f"target `{destination}`",
                )

    def _fetch_into_shell_hits(
        self, segments: Sequence[Segment], index: int, segment: Segment
    ) -> Iterator[DenyHit]:
        interprets = segment.binary in SHELL_EXECUTORS or segment.binary in CODE_INTERPRETERS
        if (
            interprets
            and segment.connector == "|"
            and index
            and segments[index - 1].binary in FETCH_BINARIES
        ):
            yield DenyHit(
                code=DenyCode.FETCH_INTO_SHELL,
                subject=segment.raw,
                detail=f"input comes from `{segments[index - 1].raw}`",
            )
        if segment.binary in EVAL_BINARIES:
            for child in segments:
                if child.parent == index and child.binary in FETCH_BINARIES:
                    yield DenyHit(
                        code=DenyCode.FETCH_INTO_SHELL,
                        subject=segment.raw,
                        detail=f"`{child.raw}` is fetched and then evaluated",
                    )

    # -- path classification ------------------------------------------------- #

    def expand(self, word: str) -> str:
        """``~``, ``$HOME`` and ``${HOME}`` resolved to the injected home directory.

        Only these three. A general variable expander would have to guess at the
        environment, and guessing is worse than declining.
        """
        home = str(self.home)
        if word == "~" or word.startswith("~/"):
            return home + word[1:]
        for token in ("${HOME}", "$HOME"):
            if word.startswith(token):
                return home + word[len(token) :]
        return word

    def resolve(self, word: str, base: Path) -> Path:
        """Absolute, normalised, and never touching the filesystem.

        ``normpath`` rather than ``resolve``: resolving follows symlinks, which needs
        the path to exist and makes the check depend on the disk. The cost is that a
        symlink pointing out of the workspace is not caught here — that is a real gap,
        and it is named in the package docstring rather than papered over.
        """
        expanded = self.expand(word)
        path = Path(expanded)
        if not path.is_absolute():
            path = base / path
        return Path(os.path.normpath(str(path)))

    def _path_hits(
        self, word: str, base: Path, *, write: bool, subject: str = ""
    ) -> Iterator[DenyHit]:
        path = self.resolve(word, base)
        if str(path).startswith("/dev/"):
            # Devices are not files with a workspace: the block-device rule and the
            # write-to-device hazard own them, and /dev/null must stay free.
            return
        display = subject or word
        parts = path.parts
        name = path.name
        secret_directory = any(part in SECRET_DIRECTORIES for part in parts)
        key_material = name.endswith(KEY_SUFFIXES)
        dotenv = name == ".env" or name.startswith(".env.")
        if key_material or (secret_directory and not name.endswith(".pub")):
            yield DenyHit(
                code=DenyCode.SECRET_WRITE if write else DenyCode.KEY_MATERIAL_READ,
                subject=display,
                detail=f"`{word}` is credential material",
            )
            return
        if write and dotenv:
            yield DenyHit(
                code=DenyCode.SECRET_WRITE,
                subject=display,
                detail=f"`{word}` holds this project's secrets",
            )
            return
        if write and not self._inside_allowed(path):
            yield DenyHit(
                code=DenyCode.OUTSIDE_WORKSPACE,
                subject=display,
                detail=f"`{word}` resolves to {path}, outside the workspace",
            )

    def _inside_allowed(self, path: Path) -> bool:
        for root in (self.workspace_root, *self.scratch_roots):
            if path == root or root in path.parents:
                return True
        return False

    def _retarget(self, cwd: Path, segment: Segment) -> Path:
        """Track ``cd`` so a later write is resolved against the right directory.

        Without this, ``cd .. && echo x > secrets`` is checked against the workspace
        root and looks like an in-workspace write.
        """
        operands = [word for word in segment.operands if word != "-"]
        if not operands:
            return self.home
        return self.resolve(operands[0], cwd)


__all__ = [
    "BLOCK_DEVICE",
    "DEFAULT_SCRATCH_ROOTS",
    "DENY_REASONS",
    "FORK_BOMB",
    "KEY_SUFFIXES",
    "SECRET_DIRECTORIES",
    "WRITE_BINARIES",
    "DenyCode",
    "DenyHit",
    "DenyReason",
    "Denylist",
]
