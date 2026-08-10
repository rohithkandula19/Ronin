"""Checkpoint isolation: the promise that the user's own repo is never touched.

`test_verify_checkpoints.py` already byte-compares `.git/index` across a
checkpoint — the honest version of the claim, and it passes. What it cannot catch
is the case where git is told to use a *different* index than the one
`--git-dir` implies, because nothing in that file sets `GIT_INDEX_FILE`. It had
**zero** occurrences in the whole test tree, and so did `SHADOW_EXCLUDES`.

That mattered. `--git-dir` and `--work-tree` do not override `GIT_INDEX_FILE`:
the variable names the index file outright. A ronin invoked from inside a git
hook — where git exports it, pointing at the real repo's index — had every
`git add` in the checkpoint store stage into the user's index. Measured against
the code before the fix: a single checkpoint turned `?? brand_new.py` into
`A  brand_new.py` in `git status` and grew `.git/index` from 137 to 217 bytes.

So the first test here is a regression test for a bug that was real, reproduced,
and fixed, not a hypothetical. The rest cover the half of this module that the
docstring calls being "loud when it is not working" — every failure branch
returns a named reason, and none of them had a test.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from verify_harness import ScriptedRunner

from ronin.verify.checkpoints import (
    CHECKPOINT_DIR,
    SHADOW_EXCLUDES,
    Availability,
    Checkpoint,
    CheckpointReason,
    CheckpointResult,
    CheckpointStore,
    DiffResult,
    RestoreResult,
    unavailable_note,
)
from ronin.verify.runner import CommandFailure, CommandOutcome, run_command

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="exercises a real local git")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EnvRecordingRunner:
    """Records the environment each git command would actually run under."""

    envs: list[Mapping[str, str] | None] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del cwd, timeout
        recorded = tuple(str(part) for part in argv)
        self.calls.append(recorded)
        self.envs.append(env)
        # A plausible sha, so `checkpoint()` gets far enough to exercise every
        # invocation rather than stopping at the first empty rev-parse.
        return CommandOutcome(argv=recorded, exit_code=0, output="abc1234567890\n")


async def init_repo(root: Path, *, gitignore: str = ".ronin/\n") -> None:
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
    await run_command(["git", "init", "--quiet"], cwd=root)
    await run_command(["git", "add", "-A"], cwd=root)
    await run_command(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-q", "-m", "i"],
        cwd=root,
    )


def ready_store(root: Path, runner: ScriptedRunner) -> CheckpointStore:
    """A store whose availability probe short-circuits to READY.

    Faking `.git/` and the shadow `HEAD` means `_probe` never shells out, so the
    failure-branch tests below need no git binary at all — unlike
    `test_verify_checkpoints.py`, which skips its whole module without one.
    """
    (root / ".git").mkdir(exist_ok=True)
    git_dir = root / CHECKPOINT_DIR
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return CheckpointStore(root, run=runner)


# --------------------------------------------------------------------------- #
# The regression: an ambient GIT_INDEX_FILE must not reach the user's index
# --------------------------------------------------------------------------- #


@needs_git
async def test_an_ambient_git_index_file_does_not_stage_into_the_users_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this module's headline promise existed to prevent, reproduced.

    Before the fix this failed on both assertions: the file was staged (`A `) and
    `.git/index` changed size. `git status` showing something the user did not do
    is the whole of "invisible in normal use" being false.
    """
    await init_repo(tmp_path)
    real_index = tmp_path / ".git" / "index"
    (tmp_path / "brand_new.py").write_text("NEW = 2\n", encoding="utf-8")

    before_index = real_index.read_bytes()
    before_status = await run_command(["git", "status", "--porcelain"], cwd=tmp_path)

    # Exactly what git exports to a hook it invokes.
    monkeypatch.setenv("GIT_INDEX_FILE", str(real_index))

    store = CheckpointStore(tmp_path)
    made = await store.checkpoint("under a git hook")
    assert made.ok, made.detail

    after_status = await run_command(["git", "status", "--porcelain"], cwd=tmp_path)
    assert real_index.read_bytes() == before_index, "the checkpoint wrote the user's index"
    assert after_status.output == before_status.output, (
        "the checkpoint changed what `git status` reports in the user's own repo"
    )
    assert "A  brand_new.py" not in after_status.output, "the checkpoint staged a file"


@needs_git
@pytest.mark.parametrize(
    "var",
    ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"],
)
async def test_other_ambient_git_locations_do_not_break_or_redirect_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    """`GIT_WORK_TREE` and `GIT_COMMON_DIR` used to make `init` fail outright.

    That was not corruption, but it cost the session its checkpoints silently,
    which the module treats as its own kind of failure: a user who believes they
    are covered and is not.
    """
    await init_repo(tmp_path)
    real_index = tmp_path / ".git" / "index"
    before_index = real_index.read_bytes()

    monkeypatch.setenv(var, str(tmp_path / ".git"))

    store = CheckpointStore(tmp_path)
    made = await store.checkpoint("hostile ambient location")
    assert made.ok, f"{var} broke checkpoints: {made.reason.value} {made.detail}"
    assert real_index.read_bytes() == before_index


async def test_every_git_invocation_pins_the_index_to_the_shadow_repo(tmp_path: Path) -> None:
    """Pinned on *every* call, not just the ones that stage.

    Asserted at the argv/env layer so it holds without a git binary, and so a
    future subcommand added to this class cannot quietly skip the pin.
    """
    (tmp_path / ".git").mkdir()
    git_dir = tmp_path / CHECKPOINT_DIR
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    recorder = EnvRecordingRunner()
    store = CheckpointStore(tmp_path, run=recorder)
    await store.checkpoint("one")
    await store.session_diff()
    await store.restore("deadbeef")

    assert recorder.envs, "no git command ran"
    expected = str(git_dir / "index")
    for env, call in zip(recorder.envs, recorder.calls, strict=True):
        assert env is not None, f"{call[:3]} ran with an inherited environment"
        assert env["GIT_INDEX_FILE"] == expected, f"{call[:3]} did not pin the index"


async def test_an_injected_env_cannot_smuggle_a_redirect_back_in(tmp_path: Path) -> None:
    """A caller who controls the env does not get to point us at another index."""
    (tmp_path / ".git").mkdir()
    git_dir = tmp_path / CHECKPOINT_DIR
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    recorder = EnvRecordingRunner()
    store = CheckpointStore(
        tmp_path,
        run=recorder,
        env={
            "PATH": "/usr/bin",
            "GIT_INDEX_FILE": "/somewhere/else/index",
            "GIT_DIR": "/somewhere/else/.git",
        },
    )
    await store.checkpoint("one")

    env = recorder.envs[0]
    assert env is not None
    assert env["GIT_INDEX_FILE"] == str(git_dir / "index")
    assert "GIT_DIR" not in env
    assert env["PATH"] == "/usr/bin", "an injected env should otherwise be preserved"


# --------------------------------------------------------------------------- #
# SHADOW_EXCLUDES — the first checkpoint must not contain the second
# --------------------------------------------------------------------------- #


@needs_git
async def test_the_shadow_repo_excludes_itself_even_when_the_user_does_not(
    tmp_path: Path,
) -> None:
    """The case the excludes exist for, which no test covered.

    Every existing checkpoint test writes a `.gitignore` containing `.ronin/`, so
    the user's own ignore file was doing the work and `info/exclude` was never
    load-bearing. In a repo that does not ignore it, a checkpoint that captured
    `.ronin/` would snapshot the previous checkpoint — growth compounding on
    every turn.
    """
    await init_repo(tmp_path, gitignore="# nothing ignored here\n")
    store = CheckpointStore(tmp_path)
    made = await store.checkpoint("first")
    assert made.ok, made.detail

    listed = await run_command(
        ["git", f"--git-dir={tmp_path / CHECKPOINT_DIR}", f"--work-tree={tmp_path}", "ls-files"],
        cwd=tmp_path,
    )
    tracked = listed.output.splitlines()
    assert "src/thing.py" in tracked, "the checkpoint captured nothing"
    assert not [p for p in tracked if p.startswith(".ronin/")], (
        f"the checkpoint captured its own store: {[p for p in tracked if '.ronin' in p]}"
    )


async def test_the_exclude_file_is_written_with_the_declared_patterns(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    store = ready_store(tmp_path, ScriptedRunner())
    await store.ensure()
    written = (tmp_path / CHECKPOINT_DIR / "info" / "exclude").read_text(encoding="utf-8")
    assert written.splitlines() == list(SHADOW_EXCLUDES)


async def test_an_unwritable_exclude_file_is_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wasteful, not wrong — so it must not take checkpoints down with it."""
    store = ready_store(tmp_path, ScriptedRunner())

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", boom)
    availability = await store.ensure()
    assert availability.available


# --------------------------------------------------------------------------- #
# "Loud when it is not working" — every failure branch names a reason
# --------------------------------------------------------------------------- #


async def test_a_repo_that_is_not_a_git_repo_degrades_with_actionable_advice(
    tmp_path: Path,
) -> None:
    store = CheckpointStore(tmp_path, run=ScriptedRunner())
    availability = await store.ensure()
    assert not availability.available
    assert availability.reason is CheckpointReason.NOT_A_REPO
    assert "git init" in availability.detail


async def test_a_missing_git_binary_degrades_rather_than_raising(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    runner = ScriptedRunner(failures={"init": CommandFailure.NOT_FOUND})
    availability = await CheckpointStore(tmp_path, run=runner).ensure()
    assert not availability.available
    assert availability.reason is CheckpointReason.NO_GIT_BINARY
    assert "everything else runs normally" in availability.detail


@pytest.mark.parametrize(
    ("needle", "reason"),
    [
        ("init --bare", CheckpointReason.INIT_FAILED),
        ("config core.bare", CheckpointReason.INIT_FAILED),
    ],
)
async def test_a_failing_setup_step_reports_init_failed(
    tmp_path: Path, needle: str, reason: CheckpointReason
) -> None:
    (tmp_path / ".git").mkdir()
    runner = ScriptedRunner(rules=((needle, 1, "fatal: cannot"),))
    availability = await CheckpointStore(tmp_path, run=runner).ensure()
    assert not availability.available
    assert availability.reason is reason
    assert availability.detail, "an unavailable store must explain itself"


async def test_the_availability_probe_is_cached(tmp_path: Path) -> None:
    """It shells out, and the answer cannot change mid-session."""
    store = ready_store(tmp_path, ScriptedRunner())
    first = await store.ensure()
    second = await store.ensure()
    assert first is second


@pytest.mark.parametrize("needle", [" add -A", " commit --allow-empty", "rev-parse HEAD"])
async def test_a_failing_checkpoint_step_names_the_command(tmp_path: Path, needle: str) -> None:
    store = ready_store(tmp_path, ScriptedRunner(rules=((needle, 1, "fatal: git said no"),)))
    result = await store.checkpoint("doomed")
    assert not result.ok
    assert result.reason is CheckpointReason.GIT_FAILED
    assert "git said no" in result.detail


async def test_a_checkpoint_survives_a_missing_timestamp(tmp_path: Path) -> None:
    """`created_at` is cosmetic; losing it must not lose the checkpoint."""
    store = ready_store(
        tmp_path,
        ScriptedRunner(rules=(("log -1", 1, "fatal: no log"), ("rev-parse HEAD", 0, "abc123\n"))),
    )
    result = await store.checkpoint("labelled")
    assert result.ok and result.checkpoint is not None
    assert result.checkpoint.created_at == ""
    assert result.checkpoint.id == "abc123"


async def test_a_commit_id_that_never_arrives_is_a_failed_result_not_an_exception(
    tmp_path: Path,
) -> None:
    """`rev-parse` exiting 0 with empty stdout used to raise `ValueError` straight
    out of the store. Every other failure in this class is a value, and a raise
    here lands on a mutating turn — the one moment the caller has no fallback."""
    store = ready_store(tmp_path, ScriptedRunner(rules=(("rev-parse HEAD", 0, "  \n"),)))
    result = await store.checkpoint("empty head")
    assert not result.ok
    assert result.reason is CheckpointReason.GIT_FAILED
    assert "no commit id" in result.detail


async def test_an_unavailable_store_reports_the_reason_from_every_entry_point(
    tmp_path: Path,
) -> None:
    """One degraded probe, four callers — none of them may raise."""
    store = CheckpointStore(tmp_path, run=ScriptedRunner())  # no .git → NOT_A_REPO
    assert (await store.checkpoint("x")).reason is CheckpointReason.NOT_A_REPO
    assert (await store.restore("x")).reason is CheckpointReason.NOT_A_REPO
    assert (await store.session_diff()).reason is CheckpointReason.NOT_A_REPO
    assert await store.history() == ()


async def test_history_is_empty_rather_than_raising_when_the_log_fails(tmp_path: Path) -> None:
    store = ready_store(tmp_path, ScriptedRunner(rules=(("log --format", 1, "fatal: no head"),)))
    assert await store.history() == ()


async def test_history_skips_malformed_lines(tmp_path: Path) -> None:
    """A truncated log line yields no checkpoint rather than a half-built one."""
    good = "abc\x1flabel\x1f2026-01-01T00:00:00Z"
    store = ready_store(
        tmp_path, ScriptedRunner(rules=(("log --format", 0, f"{good}\nnot-a-record\n\n"),))
    )
    history = await store.history()
    assert len(history) == 1
    assert history[0].id == "abc"


async def test_restoring_without_an_id_is_refused_by_name(tmp_path: Path) -> None:
    store = ready_store(tmp_path, ScriptedRunner())
    result = await store.restore("")
    assert not result.ok
    assert result.reason is CheckpointReason.UNKNOWN_CHECKPOINT


async def test_restoring_an_unknown_id_names_the_store_it_looked_in(tmp_path: Path) -> None:
    store = ready_store(tmp_path, ScriptedRunner(rules=(("rev-parse --verify", 1, "bad rev"),)))
    result = await store.restore("nope")
    assert not result.ok
    assert result.reason is CheckpointReason.UNKNOWN_CHECKPOINT
    assert CHECKPOINT_DIR in result.detail


async def test_a_failing_reset_reports_git_failed(tmp_path: Path) -> None:
    store = ready_store(
        tmp_path,
        ScriptedRunner(
            rules=(("rev-parse --verify", 0, "abc123\n"), ("reset --hard", 1, "fatal: locked")),
        ),
    )
    result = await store.restore("abc123")
    assert not result.ok
    assert result.reason is CheckpointReason.GIT_FAILED
    assert "locked" in result.detail


async def test_a_failing_clean_admits_the_restore_was_partial(tmp_path: Path) -> None:
    """The most important failure message in the module.

    Tracked files *are* back but new ones are still there, so the tree matches
    neither state. Reporting a flat failure would send the user looking for an
    undo that already half-happened; reporting success would be a lie.
    """
    store = ready_store(
        tmp_path,
        ScriptedRunner(
            rules=(
                ("rev-parse --verify", 0, "abc1234567890\n"),
                ("clean -fdq", 1, "fatal: permission denied"),
            ),
        ),
    )
    result = await store.restore("abc1234567890")
    assert not result.ok
    assert result.reason is CheckpointReason.GIT_FAILED
    assert "restored tracked files" in result.detail
    assert "could not remove" in result.detail
    assert "abc1234567" in result.detail, "the user needs the id they landed on"


async def test_a_session_diff_without_a_checkpoint_says_there_is_no_base(tmp_path: Path) -> None:
    store = ready_store(tmp_path, ScriptedRunner())
    result = await store.session_diff()
    assert not result.ok
    assert result.reason is CheckpointReason.NO_CHECKPOINTS


@pytest.mark.parametrize("needle", [" add -A", "diff --cached"])
async def test_a_failing_session_diff_step_reports_git_failed(tmp_path: Path, needle: str) -> None:
    store = ready_store(tmp_path, ScriptedRunner(rules=((needle, 1, "fatal: diff broke"),)))
    result = await store.session_diff(base="abc123")
    assert not result.ok
    assert result.reason is CheckpointReason.GIT_FAILED
    assert "diff broke" in result.detail


async def test_the_session_base_is_the_first_checkpoint_not_the_latest(tmp_path: Path) -> None:
    """`/diff` means "everything this session changed", so the anchor must not move."""
    runner = ScriptedRunner(rules=(("rev-parse HEAD", 0, "first\n"),))
    store = ready_store(tmp_path, runner)

    # Every read is captured before anything is asserted: `assert x is None`
    # narrows the property for the rest of the function, which makes a later
    # comparison against a string provably false rather than merely failing.
    before = store.session_base
    await store.checkpoint("one")
    after_first = store.session_base
    runner.rules = (("rev-parse HEAD", 0, "second\n"),)
    await store.checkpoint("two")
    after_second = store.session_base

    assert before is None
    assert after_first == "first"
    assert after_second == "first", "the /diff anchor moved to the newest checkpoint"


async def test_an_explicit_base_overrides_the_session_anchor(tmp_path: Path) -> None:
    store = ready_store(tmp_path, ScriptedRunner(rules=(("diff --cached", 0, "patch"),)))
    result = await store.session_diff(base="chosen")
    assert result.ok
    assert result.base == "chosen"


# --------------------------------------------------------------------------- #
# The result types' own invariants
# --------------------------------------------------------------------------- #


def test_an_unavailable_store_cannot_be_silent() -> None:
    """The validator behind "a silent degradation is how a user loses work
    believing they were covered" — asserted, so removing the check fails a test."""
    with pytest.raises(ValueError, match="silent degradation"):
        Availability(available=False, reason=CheckpointReason.NOT_A_REPO)
    assert Availability(available=True, reason=CheckpointReason.READY).detail == ""


@pytest.mark.parametrize("kind", [CheckpointResult, RestoreResult, DiffResult])
def test_a_failed_result_must_say_why(
    kind: type[CheckpointResult] | type[RestoreResult] | type[DiffResult],
) -> None:
    with pytest.raises(ValueError):
        kind(ok=False)
    assert kind(ok=True).ok


def test_a_checkpoint_requires_an_id_and_shortens_it_for_display() -> None:
    with pytest.raises(ValueError, match="id is required"):
        Checkpoint(id="", label="x")
    assert Checkpoint(id="0123456789abcdef", label="x").short == "0123456789"


def test_an_empty_diff_is_reported_as_empty_not_as_a_failure() -> None:
    assert DiffResult(ok=True, diff="   \n\n").empty
    assert not DiffResult(ok=True, diff="--- a/x\n").empty


def test_the_unavailable_note_is_blank_when_checkpoints_work() -> None:
    assert unavailable_note(Availability(available=True, reason=CheckpointReason.READY)) == ""
    note = unavailable_note(
        Availability(
            available=False, reason=CheckpointReason.NO_GIT_BINARY, detail="git is not installed"
        )
    )
    assert "no_git_binary" in note
    assert "git is not installed" in note
