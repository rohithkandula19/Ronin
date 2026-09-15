"""The post a Retainer holds: a checkout on disk, and how it gets there.

``docs/RETAINER.md`` §8 step 8, and where decision 4 is answered: **one
workspace per repo per Retainer.** A Retainer working across three repositories
holds three checkouts. It costs disk and buys two things worth more than disk —
a half-finished edit in one repo can never be visible to a run about another,
and a corrupt checkout is re-cloned without touching the rest.

The layout is nested rather than flattened: ``<root>/<retainer>/<owner>/<name>``.
An earlier draft joined the repo with a separator, and ``a__b/c`` and
``a/b__c`` collide under any separator you pick — two different repositories
sharing one directory, which is the isolation this step exists to provide,
silently gone. Nesting has no such case.

**Repository names arrive from webhooks, so they are path input.** Every segment
is checked against a strict pattern before it is joined, and ``.`` and ``..`` are
refused by name. GitHub allows dots in repository names — ``foo.js`` is ordinary
— so a blanket ban on dots would be wrong; what must be impossible is a segment
that means "somewhere else".

**Identity goes on the command line, not into config.** Decision 5 is answered
too: a Retainer commits as **its own bot identity**, and that identity is passed
as ``git -c user.name=… -c user.email=…`` per invocation. Command-line ``-c``
outranks every config file, so a cloned repository cannot make the Retainer's
commits appear to come from somebody else by shipping a ``.git/config``. Writing
the identity into the checkout would be one file away from being edited by the
very code the Retainer is running.

**Nothing here shells out on its own.** The command runner is injected, exactly
as ``verify.checkpoints`` injects it, so the whole module is testable with no
git and no network — which the repository requires of every test.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from ronin.retainer.model import Post
from ronin.verify.runner import CommandFailure, CommandOutcome, CommandRunner, run_command

#: One path segment of a repository coordinate. Dots are allowed because
#: ``foo.js`` is an ordinary repository name; ``.`` and ``..`` are refused
#: separately, by name.
SEGMENT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

#: Segments that name somewhere else rather than something.
RESERVED: Final = frozenset({".", ".."})

DEFAULT_TIMEOUT_SECONDS: Final = 300.0

#: Git environment variables that would redirect a command at another
#: repository. Cleared for the same reason ``verify.checkpoints`` clears them:
#: a Retainer's clone must not be steerable by whatever the daemon inherited.
_INHERITED_GIT_VARS: Final[tuple[str, ...]] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_INDEX_FILE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
)


class PostError(RuntimeError):
    """A post could not be created, refreshed or discarded."""


class PostState(StrEnum):
    """What :meth:`PostStore.ensure` had to do."""

    CLONED = "cloned"
    """It was not there. It is now."""

    ADOPTED = "adopted"
    """It was already there and was left exactly as found."""


@dataclass(frozen=True, slots=True)
class Identity:
    """Who a Retainer's commits come from.

    Its own, not the operator's: separate permissions, and an audit trail that
    says plainly which commits were the agent's. Revoking it does not revoke the
    operator.
    """

    name: str
    email: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.email.strip():
            raise ValueError("Identity needs both a name and an email")
        if "\n" in self.name or "\n" in self.email:
            raise ValueError("Identity must be one line — a newline would forge a header")

    @property
    def git_args(self) -> tuple[str, ...]:
        """``-c user.name=… -c user.email=…``, which outranks every config file."""
        return ("-c", f"user.name={self.name}", "-c", f"user.email={self.email}")


@dataclass(frozen=True, slots=True)
class Held:
    """A post and what happened to get it."""

    post: Post
    state: PostState

    @property
    def fresh(self) -> bool:
        return self.state is PostState.CLONED


def _segment(value: str, *, what: str) -> str:
    """One validated path segment, or a refusal naming what was wrong."""
    if value in RESERVED:
        raise PostError(f"{what} {value!r} names a directory, not a repository")
    if not SEGMENT.match(value):
        raise PostError(
            f"{what} {value!r} is not usable as a directory name — it must start "
            "with a letter or digit and contain only letters, digits, dot, dash "
            "or underscore"
        )
    return value


def workspace_for(root: Path, retainer: str, repo: str) -> Path:
    """Where this Retainer's checkout of this repository lives.

    Nested, never joined with a separator: see the module docstring for the
    collision that makes flattening wrong.
    """
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        raise PostError(f"{repo!r} must be 'owner/name'")
    return (
        root
        / _segment(retainer, what="retainer id")
        / _segment(owner, what="repository owner")
        / _segment(name, what="repository name")
    )


@dataclass(frozen=True, slots=True)
class PostStore:
    """Every post under one root, one per repo per Retainer."""

    root: Path
    identity: Identity
    run: CommandRunner = run_command
    git: str = "git"
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    remote: str = "https://github.com"
    environ: Mapping[str, str] | None = None
    """The environment git runs under. ``None`` means a minimal one, never the
    daemon's own — an inherited ``GIT_DIR`` would point every command elsewhere."""

    def _env(self) -> dict[str, str]:
        base = dict(self.environ or {})
        for name in _INHERITED_GIT_VARS:
            base.pop(name, None)
        # Never prompt: a Retainer has no terminal, and a git that asks for a
        # password does not fail, it hangs until the timeout.
        base.setdefault("GIT_TERMINAL_PROMPT", "0")
        base.setdefault("GIT_ASKPASS", "")
        return base

    async def _git(self, argv: Sequence[str], *, cwd: Path) -> CommandOutcome:
        return await self.run(
            [self.git, *self.identity.git_args, *argv],
            cwd=cwd,
            timeout=self.timeout,
            env=self._env(),
        )

    @staticmethod
    def _check(outcome: CommandOutcome, *, doing: str) -> CommandOutcome:
        if outcome.failure is CommandFailure.NOT_FOUND:
            raise PostError(f"cannot {doing}: no git binary on this deployment")
        if outcome.failure is CommandFailure.TIMED_OUT:
            raise PostError(f"cannot {doing}: git timed out")
        if outcome.failure is not CommandFailure.NONE or outcome.exit_code != 0:
            raise PostError(f"cannot {doing}: git exited {outcome.exit_code}\n{outcome.output}")
        return outcome

    def url_for(self, repo: str) -> str:
        """The clone URL. Separate so a test never needs a real remote."""
        return f"{self.remote.rstrip('/')}/{repo}.git"

    async def ensure(self, retainer: str, repo: str, *, branch: str = "") -> Held:
        """The Retainer's checkout of ``repo``, cloning it only if it is absent.

        Adopts an existing checkout untouched. It may hold work in progress from
        a run that escalated and has not resumed yet, and quietly resetting that
        would destroy the state an approval is about to be applied to —
        :meth:`refresh` exists for when discarding it is the intention.
        """
        path = workspace_for(self.root, retainer, repo)
        if (path / ".git").is_dir():
            return Held(
                post=Post(repo=repo, workspace=path, branch=branch), state=PostState.ADOPTED
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        argv = ["clone", self.url_for(repo), str(path)]
        if branch:
            argv[1:1] = ["--branch", branch]
        self._check(await self._git(argv, cwd=path.parent), doing=f"clone {repo}")
        if not (path / ".git").is_dir():
            raise PostError(f"cannot clone {repo}: git reported success but {path} is not a repo")
        return Held(post=Post(repo=repo, workspace=path, branch=branch), state=PostState.CLONED)

    async def refresh(self, post: Post, *, branch: str = "") -> Post:
        """Fetch and hard-reset the checkout, discarding anything uncommitted.

        Destructive, explicitly and by name, because the alternative — folding
        this into :meth:`ensure` — throws away a paused run's work without
        anybody asking for it.
        """
        target = branch or post.branch
        if not target:
            raise PostError(f"cannot refresh {post.repo}: no branch to reset to")
        self._check(
            await self._git(["fetch", "--prune", "origin"], cwd=post.workspace),
            doing=f"fetch {post.repo}",
        )
        self._check(
            await self._git(["reset", "--hard", f"origin/{target}"], cwd=post.workspace),
            doing=f"reset {post.repo} to origin/{target}",
        )
        self._check(
            await self._git(["clean", "-fd"], cwd=post.workspace),
            doing=f"clean {post.repo}",
        )
        return Post(repo=post.repo, workspace=post.workspace, branch=target)

    def discard(self, retainer: str, repo: str) -> bool:
        """Delete a checkout so the next :meth:`ensure` re-clones it.

        For a workspace that git itself cannot repair. Returns whether there was
        anything to delete, so a caller can tell "cleaned up" from "already gone"
        rather than inferring it.
        """
        path = workspace_for(self.root, retainer, repo)
        if not path.exists():
            return False
        if not (path / ".git").is_dir():
            raise PostError(
                f"refusing to delete {path}: it is not a git checkout, and this "
                "method only removes posts it could have created"
            )
        shutil.rmtree(path)
        return True

    def held_by(self, retainer: str) -> tuple[str, ...]:
        """Which repositories this Retainer has a checkout of, as ``owner/name``."""
        base = self.root / _segment(retainer, what="retainer id")
        if not base.is_dir():
            return ()
        found = [
            f"{owner.name}/{name.name}"
            for owner in sorted(base.iterdir())
            if owner.is_dir()
            for name in sorted(owner.iterdir())
            if (name / ".git").is_dir()
        ]
        return tuple(found)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "RESERVED",
    "SEGMENT",
    "Held",
    "Identity",
    "PostError",
    "PostState",
    "PostStore",
    "workspace_for",
]
