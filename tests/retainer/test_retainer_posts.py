"""The post a Retainer holds: one workspace per repo per Retainer.

Two tests here guard properties that are silent when broken.
``test_two_repositories_cannot_collide_on_one_directory`` pins why the layout is
nested rather than joined with a separator; ``test_the_identity_outranks_a_repos_own_config``
pins that a cloned repository cannot make the Retainer's commits appear to come
from somebody else.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from ronin.retainer.model import Post
from ronin.retainer.posts import (
    RESERVED,
    Held,
    Identity,
    PostError,
    PostState,
    PostStore,
    workspace_for,
)
from ronin.verify.runner import CommandFailure, CommandOutcome

BOT = Identity(name="ronin[bot]", email="ronin@users.noreply.github.com")
REPO = "rohithkandula19/Ronin"


class FakeGit:
    """Records every invocation and pretends to clone by making a .git directory."""

    def __init__(
        self, *, exit_code: int = 0, failure: CommandFailure = CommandFailure.NONE
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], Path, Mapping[str, str]]] = []
        self.exit_code = exit_code
        self.failure = failure
        self.create = True

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        self.calls.append((tuple(argv), cwd, dict(env or {})))
        if self.create and self.exit_code == 0 and "clone" in argv:
            (Path(argv[-1]) / ".git").mkdir(parents=True, exist_ok=True)
        return CommandOutcome(
            argv=tuple(argv),
            exit_code=self.exit_code,
            output="fatal: something" if self.exit_code else "",
            failure=self.failure,
        )

    @property
    def argvs(self) -> list[tuple[str, ...]]:
        return [call[0] for call in self.calls]


def store(tmp_path: Path, git: FakeGit | None = None, **kwargs: Any) -> PostStore:
    return PostStore(root=tmp_path / "posts", identity=BOT, run=git or FakeGit(), **kwargs)


def held(tmp_path: Path, git: FakeGit | None = None, **kwargs: Any) -> Held:
    keeper = store(tmp_path, git, **kwargs)
    return asyncio.run(keeper.ensure("sentry", REPO))


# --------------------------------------------------------------------------- #
# One workspace per repo per Retainer
# --------------------------------------------------------------------------- #


def test_a_retainer_gets_one_workspace_per_repository(tmp_path: Path) -> None:
    keeper = store(tmp_path)
    one = asyncio.run(keeper.ensure("sentry", "o/alpha"))
    two = asyncio.run(keeper.ensure("sentry", "o/beta"))
    assert one.post.workspace != two.post.workspace


def test_two_retainers_do_not_share_a_checkout(tmp_path: Path) -> None:
    keeper = store(tmp_path)
    mine = asyncio.run(keeper.ensure("sentry", REPO))
    yours = asyncio.run(keeper.ensure("scout", REPO))
    assert mine.post.workspace != yours.post.workspace


def test_two_repositories_cannot_collide_on_one_directory(tmp_path: Path) -> None:
    """Why the layout is nested: any separator makes these two the same path."""
    root = tmp_path / "posts"
    first = workspace_for(root, "sentry", "a__b/c")
    second = workspace_for(root, "sentry", "a/b__c")
    assert first != second


def test_the_layout_is_retainer_then_owner_then_name(tmp_path: Path) -> None:
    path = workspace_for(tmp_path, "sentry", "rohithkandula19/Ronin")
    assert path == tmp_path / "sentry" / "rohithkandula19" / "Ronin"


# --------------------------------------------------------------------------- #
# Repository names are path input
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "..", ".", "-rf", ""])
def test_a_segment_that_names_somewhere_else_is_refused(hostile: str) -> None:
    with pytest.raises(PostError):
        workspace_for(Path("/posts"), "sentry", f"owner/{hostile}")
    with pytest.raises(PostError):
        workspace_for(Path("/posts"), "sentry", f"{hostile}/name")


def test_a_dot_in_a_repository_name_is_ordinary(tmp_path: Path) -> None:
    """`foo.js` is a real repository name; banning dots outright would be wrong."""
    path = workspace_for(tmp_path, "sentry", "owner/foo.js")
    assert path.name == "foo.js"


def test_the_reserved_names_are_the_two_that_mean_elsewhere() -> None:
    assert {".", ".."} == RESERVED


def test_a_repo_that_is_not_owner_slash_name_is_refused() -> None:
    for bad in ("Ronin", "a/b/c", "/Ronin", "Ronin/"):
        with pytest.raises(PostError, match="owner/name"):
            workspace_for(Path("/posts"), "sentry", bad)


def test_a_hostile_retainer_id_is_refused_too(tmp_path: Path) -> None:
    with pytest.raises(PostError, match="retainer id"):
        workspace_for(tmp_path, "../escape", REPO)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_the_identity_outranks_a_repos_own_config(tmp_path: Path) -> None:
    """`-c` on the command line beats every config file, including a cloned one."""
    git = FakeGit()
    held(tmp_path, git)
    argv = git.argvs[0]
    assert argv[0] == "git"
    assert argv[1:5] == (
        "-c",
        "user.name=ronin[bot]",
        "-c",
        "user.email=ronin@users.noreply.github.com",
    )
    assert argv.index("-c") < argv.index("clone")


def test_an_identity_needs_both_halves() -> None:
    with pytest.raises(ValueError, match="both a name and an email"):
        Identity(name="", email="a@b")
    with pytest.raises(ValueError, match="both a name and an email"):
        Identity(name="a", email="  ")


def test_an_identity_may_not_contain_a_newline() -> None:
    with pytest.raises(ValueError, match="forge a header"):
        Identity(name="ronin\nCommitter: someone", email="a@b")


# --------------------------------------------------------------------------- #
# The environment git runs under
# --------------------------------------------------------------------------- #


def test_an_inherited_git_dir_cannot_redirect_the_clone(tmp_path: Path) -> None:
    git = FakeGit()
    held(tmp_path, git, environ={"GIT_DIR": "/somewhere/else", "PATH": "/usr/bin"})
    _argv, _cwd, env = git.calls[0]
    assert "GIT_DIR" not in env
    assert env["PATH"] == "/usr/bin"


def test_git_is_told_never_to_prompt(tmp_path: Path) -> None:
    """A git that asks for a password does not fail — it hangs until the timeout."""
    git = FakeGit()
    held(tmp_path, git)
    _argv, _cwd, env = git.calls[0]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == ""


def test_nothing_of_the_daemons_environment_leaks_in_by_default(tmp_path: Path) -> None:
    git = FakeGit()
    held(tmp_path, git)
    _argv, _cwd, env = git.calls[0]
    assert set(env) == {"GIT_TERMINAL_PROMPT", "GIT_ASKPASS"}


# --------------------------------------------------------------------------- #
# ensure(): clone once, then adopt
# --------------------------------------------------------------------------- #


def test_the_first_call_clones(tmp_path: Path) -> None:
    git = FakeGit()
    result = held(tmp_path, git)
    assert result.state is PostState.CLONED
    assert result.fresh
    assert any("clone" in argv for argv in git.argvs)


def test_the_second_call_adopts_without_touching_git(tmp_path: Path) -> None:
    """An adopted post may hold a paused run's work. Resetting it would destroy that."""
    git = FakeGit()
    keeper = store(tmp_path, git)
    asyncio.run(keeper.ensure("sentry", REPO))
    before = len(git.calls)
    again = asyncio.run(keeper.ensure("sentry", REPO))
    assert again.state is PostState.ADOPTED
    assert not again.fresh
    assert len(git.calls) == before


def test_a_branch_is_passed_to_the_clone(tmp_path: Path) -> None:
    git = FakeGit()
    keeper = store(tmp_path, git)
    result = asyncio.run(keeper.ensure("sentry", REPO, branch="main"))
    # git -c name -c email clone --branch main <url> <path>
    assert git.argvs[0][5:8] == ("clone", "--branch", "main")
    assert result.post.branch == "main"


def test_the_clone_url_is_built_from_the_configured_remote(tmp_path: Path) -> None:
    keeper = store(tmp_path, remote="https://git.example.com/")
    assert keeper.url_for(REPO) == f"https://git.example.com/{REPO}.git"


def test_a_git_that_is_not_installed_says_so(tmp_path: Path) -> None:
    git = FakeGit(exit_code=127, failure=CommandFailure.NOT_FOUND)
    with pytest.raises(PostError, match="no git binary"):
        held(tmp_path, git)


def test_a_git_that_times_out_says_so(tmp_path: Path) -> None:
    git = FakeGit(exit_code=124, failure=CommandFailure.TIMED_OUT)
    with pytest.raises(PostError, match="timed out"):
        held(tmp_path, git)


def test_a_failed_clone_carries_gits_own_words(tmp_path: Path) -> None:
    git = FakeGit(exit_code=128)
    with pytest.raises(PostError, match="fatal: something"):
        held(tmp_path, git)


def test_a_clone_that_claims_success_but_made_nothing_is_an_error(tmp_path: Path) -> None:
    """Trusting the exit code alone would hand back a Post pointing at nothing."""
    git = FakeGit()
    git.create = False
    with pytest.raises(PostError, match="is not a repo"):
        held(tmp_path, git)


# --------------------------------------------------------------------------- #
# refresh(): destructive, explicitly and by name
# --------------------------------------------------------------------------- #


def test_refresh_fetches_resets_and_cleans(tmp_path: Path) -> None:
    git = FakeGit()
    keeper = store(tmp_path, git)
    result = asyncio.run(keeper.ensure("sentry", REPO, branch="main"))
    git.calls.clear()
    refreshed = asyncio.run(keeper.refresh(result.post))
    verbs = [argv[5] for argv in git.argvs]
    assert verbs == ["fetch", "reset", "clean"]
    assert "origin/main" in git.argvs[1]
    assert refreshed.branch == "main"


def test_refresh_can_be_pointed_at_another_branch(tmp_path: Path) -> None:
    git = FakeGit()
    keeper = store(tmp_path, git)
    result = asyncio.run(keeper.ensure("sentry", REPO, branch="main"))
    refreshed = asyncio.run(keeper.refresh(result.post, branch="develop"))
    assert refreshed.branch == "develop"
    assert any("origin/develop" in argv for argv in git.argvs)


def test_refresh_with_no_branch_anywhere_refuses_rather_than_guessing(
    tmp_path: Path,
) -> None:
    keeper = store(tmp_path)
    post = Post(repo=REPO, workspace=tmp_path / "anywhere")
    with pytest.raises(PostError, match="no branch to reset to"):
        asyncio.run(keeper.refresh(post))


def test_a_failing_fetch_stops_before_the_reset(tmp_path: Path) -> None:
    """A reset against a stale origin is worse than not refreshing at all."""
    git = FakeGit()
    keeper = store(tmp_path, git)
    result = asyncio.run(keeper.ensure("sentry", REPO, branch="main"))
    git.calls.clear()
    git.exit_code = 1
    with pytest.raises(PostError, match="cannot fetch"):
        asyncio.run(keeper.refresh(result.post))
    assert [argv[5] for argv in git.argvs] == ["fetch"]


# --------------------------------------------------------------------------- #
# discard() and inspection
# --------------------------------------------------------------------------- #


def test_discarding_removes_the_checkout_and_reports_it(tmp_path: Path) -> None:
    keeper = store(tmp_path)
    result = asyncio.run(keeper.ensure("sentry", REPO))
    assert keeper.discard("sentry", REPO)
    assert not result.post.workspace.exists()
    assert not keeper.discard("sentry", REPO)


def test_the_next_ensure_re_clones_a_discarded_post(tmp_path: Path) -> None:
    git = FakeGit()
    keeper = store(tmp_path, git)
    asyncio.run(keeper.ensure("sentry", REPO))
    keeper.discard("sentry", REPO)
    assert asyncio.run(keeper.ensure("sentry", REPO)).state is PostState.CLONED


def test_discard_refuses_a_directory_it_could_not_have_created(tmp_path: Path) -> None:
    """Only removes posts, so a mis-typed argument cannot delete somebody's work."""
    path = workspace_for(tmp_path / "posts", "sentry", REPO)
    path.mkdir(parents=True)
    (path / "important.txt").write_text("not a checkout")
    keeper = store(tmp_path)
    with pytest.raises(PostError, match="refusing to delete"):
        keeper.discard("sentry", REPO)
    assert (path / "important.txt").exists()


def test_held_by_lists_what_a_retainer_actually_has(tmp_path: Path) -> None:
    keeper = store(tmp_path)
    asyncio.run(keeper.ensure("sentry", "o/beta"))
    asyncio.run(keeper.ensure("sentry", "o/alpha"))
    asyncio.run(keeper.ensure("scout", "o/gamma"))
    assert keeper.held_by("sentry") == ("o/alpha", "o/beta")
    assert keeper.held_by("scout") == ("o/gamma",)


def test_held_by_is_empty_for_a_retainer_with_no_posts(tmp_path: Path) -> None:
    assert store(tmp_path).held_by("nobody") == ()


def test_held_by_ignores_a_directory_that_is_not_a_checkout(tmp_path: Path) -> None:
    keeper = store(tmp_path)
    asyncio.run(keeper.ensure("sentry", "o/real"))
    (keeper.root / "sentry" / "o" / "not-a-repo").mkdir(parents=True)
    (keeper.root / "sentry" / "stray-file").write_text("x")
    assert keeper.held_by("sentry") == ("o/real",)
