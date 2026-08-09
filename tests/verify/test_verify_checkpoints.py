"""Checkpoints: recoverable, byte-exact, and invisible in the user's own repo.

The git-backed tests run a real local ``git``; they are offline and touch nothing
outside ``tmp_path``. Where a failure mode cannot be produced locally (no git
binary, a failing ``git commit``) the runner is scripted instead.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from verify_harness import ScriptedRunner

from ronin.verify.checkpoints import (
    CheckpointReason,
    CheckpointStore,
    changed_paths_from_diff,
    unavailable_note,
)
from ronin.verify.runner import CommandFailure, run_command

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="these tests exercise a real local git"
)


async def _init_repo(root: Path) -> None:
    """A tiny real repo with one commit, so the store's preconditions hold."""
    (root / ".gitignore").write_text(".ronin/\nsecret.txt\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "secret.txt").write_text("do not touch me\n", encoding="utf-8")
    await run_command(["git", "init", "--quiet"], cwd=root)
    await run_command(["git", "add", "-A"], cwd=root)
    await run_command(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@localhost",
            "commit",
            "--quiet",
            "-m",
            "initial",
        ],
        cwd=root,
    )


async def test_a_checkpoint_and_restore_round_trips_the_work_tree(tmp_path: Path) -> None:
    await _init_repo(tmp_path)
    store = CheckpointStore(tmp_path)
    target = tmp_path / "src" / "thing.py"
    original = target.read_bytes()

    made = await store.checkpoint("before the edit")
    assert made.ok and made.checkpoint is not None

    target.write_bytes(b"VALUE = 2\n")
    (tmp_path / "src" / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")

    restored = await store.restore(made.checkpoint.id)

    assert restored.ok
    assert restored.restored_to == made.checkpoint.id
    assert target.read_bytes() == original
    assert not (tmp_path / "src" / "extra.py").exists()


async def test_the_users_index_and_status_are_untouched(tmp_path: Path) -> None:
    """The invariant the whole design exists for, asserted on bytes.

    If this ever fails, a user running ``git status`` after a turn sees staged
    changes they did not make — which is the single fastest way to lose their
    trust in an agent that edits files.
    """
    await _init_repo(tmp_path)
    real_index = tmp_path / ".git" / "index"
    real_head = tmp_path / ".git" / "HEAD"
    before_index = real_index.read_bytes()
    before_mtime = real_index.stat().st_mtime_ns
    before_head = real_head.read_bytes()
    before_status = await run_command(["git", "status", "--porcelain"], cwd=tmp_path)

    store = CheckpointStore(tmp_path)
    made = await store.checkpoint("mutating turn")
    assert made.ok and made.checkpoint is not None
    (tmp_path / "src" / "thing.py").write_bytes(b"VALUE = 3\n")
    await store.session_diff()
    await store.restore(made.checkpoint.id)

    after_status = await run_command(["git", "status", "--porcelain"], cwd=tmp_path)
    assert real_index.read_bytes() == before_index
    assert real_index.stat().st_mtime_ns == before_mtime
    assert real_head.read_bytes() == before_head
    assert after_status.output == before_status.output
    # …and no branch, stash or reflog entry appeared either.
    branches = await run_command(["git", "branch", "--list"], cwd=tmp_path)
    assert "ronin" not in branches.output


async def test_gitignored_files_are_neither_snapshotted_nor_deleted(tmp_path: Path) -> None:
    await _init_repo(tmp_path)
    store = CheckpointStore(tmp_path)
    made = await store.checkpoint("first")
    assert made.ok

    tracked = await run_command(
        ["git", f"--git-dir={store.git_dir}", f"--work-tree={tmp_path}", "ls-files"],
        cwd=tmp_path,
    )
    assert "secret.txt" not in tracked.output
    # The shadow repo must never contain itself.
    assert ".ronin" not in tracked.output
    assert "src/thing.py" in tracked.output

    # An ignored file created after the checkpoint survives a restore: undo must
    # not turn into "reinstall your virtualenv".
    (tmp_path / "secret.txt").write_text("still here\n", encoding="utf-8")
    assert made.checkpoint is not None
    await store.restore(made.checkpoint.id)
    assert (tmp_path / "secret.txt").read_text(encoding="utf-8") == "still here\n"


async def test_line_endings_survive_a_restore_byte_for_byte(tmp_path: Path) -> None:
    """``core.autocrlf`` in the user's global config must not rewrite the tree."""
    await _init_repo(tmp_path)
    crlf = tmp_path / "src" / "windows.py"
    crlf.write_bytes(b"A = 1\r\nB = 2\r\n")
    store = CheckpointStore(tmp_path)
    made = await store.checkpoint("with crlf")
    assert made.ok and made.checkpoint is not None

    crlf.write_bytes(b"A = 9\n")
    await store.restore(made.checkpoint.id)

    assert crlf.read_bytes() == b"A = 1\r\nB = 2\r\n"


async def test_the_session_diff_includes_files_created_after_the_checkpoint(
    tmp_path: Path,
) -> None:
    await _init_repo(tmp_path)
    store = CheckpointStore(tmp_path)
    assert (await store.checkpoint("base")).ok

    (tmp_path / "src" / "thing.py").write_text("VALUE = 42\n", encoding="utf-8")
    (tmp_path / "src" / "new.py").write_text("NEW = True\n", encoding="utf-8")

    diff = await store.session_diff()

    assert diff.ok
    assert not diff.empty
    assert diff.base == store.session_base
    assert set(changed_paths_from_diff(diff.diff)) == {"src/thing.py", "src/new.py"}


async def test_the_session_diff_is_anchored_to_the_first_checkpoint(tmp_path: Path) -> None:
    await _init_repo(tmp_path)
    store = CheckpointStore(tmp_path)
    first = await store.checkpoint("turn one")
    (tmp_path / "src" / "thing.py").write_text("VALUE = 2\n", encoding="utf-8")
    await store.checkpoint("turn two")
    (tmp_path / "src" / "thing.py").write_text("VALUE = 3\n", encoding="utf-8")

    diff = await store.session_diff()

    assert first.checkpoint is not None
    assert diff.base == first.checkpoint.id
    assert "-VALUE = 1" in diff.diff
    assert "+VALUE = 3" in diff.diff


async def test_history_is_newest_first(tmp_path: Path) -> None:
    await _init_repo(tmp_path)
    store = CheckpointStore(tmp_path)
    await store.checkpoint("one")
    (tmp_path / "src" / "thing.py").write_text("VALUE = 2\n", encoding="utf-8")
    await store.checkpoint("two")

    history = await store.history()

    assert [checkpoint.label for checkpoint in history] == ["two", "one"]
    assert all(checkpoint.created_at for checkpoint in history)


async def test_restoring_an_unknown_id_is_a_named_refusal(tmp_path: Path) -> None:
    await _init_repo(tmp_path)
    store = CheckpointStore(tmp_path)
    await store.checkpoint("one")

    result = await store.restore("0" * 40)

    assert result.ok is False
    assert result.reason is CheckpointReason.UNKNOWN_CHECKPOINT
    assert "is not a checkpoint" in result.detail


async def test_a_diff_before_any_checkpoint_says_there_is_no_base(tmp_path: Path) -> None:
    await _init_repo(tmp_path)
    result = await CheckpointStore(tmp_path).session_diff()
    assert result.ok is False
    assert result.reason is CheckpointReason.NO_CHECKPOINTS


async def test_a_second_store_reuses_the_existing_shadow_repo(tmp_path: Path) -> None:
    await _init_repo(tmp_path)
    first = await CheckpointStore(tmp_path).checkpoint("one")
    assert first.ok

    second = CheckpointStore(tmp_path)
    assert (await second.ensure()).available
    history = await second.history()
    assert [checkpoint.label for checkpoint in history] == ["one"]


# --------------------------------------------------------------------------- #
# Degradation — the paths that must not crash a session
# --------------------------------------------------------------------------- #


async def test_a_directory_that_is_not_a_repo_degrades_with_a_reason(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path)

    availability = await store.ensure()

    assert availability.available is False
    assert availability.reason is CheckpointReason.NOT_A_REPO
    assert "git init" in availability.detail
    assert "not a git repository" in unavailable_note(availability)
    # Every operation still returns a value rather than raising.
    assert (await store.checkpoint("x")).ok is False
    assert (await store.restore("abc")).ok is False
    assert (await store.session_diff()).ok is False


async def test_a_missing_git_binary_degrades_with_a_reason(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    runner = ScriptedRunner(failures={"init": CommandFailure.NOT_FOUND})

    availability = await CheckpointStore(tmp_path, run=runner).ensure()

    assert availability.reason is CheckpointReason.NO_GIT_BINARY
    assert "checkpoints are off" in availability.detail
    assert "everything else runs normally" in availability.detail


async def test_a_failing_git_commit_is_reported_not_raised(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / CheckpointStore(tmp_path).git_dir.name).mkdir(exist_ok=True)
    runner = ScriptedRunner(rules=(("commit", 128, "fatal: unable to write new index file"),))
    store = CheckpointStore(tmp_path, run=runner)

    result = await store.checkpoint("turn")

    assert result.ok is False
    assert result.reason is CheckpointReason.GIT_FAILED
    assert "unable to write new index file" in result.detail


async def test_the_availability_probe_runs_once(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    runner = ScriptedRunner()
    store = CheckpointStore(tmp_path, run=runner)

    await store.ensure()
    await store.ensure()

    inits = [call for call in runner.calls if "init" in call]
    assert len(inits) == 1


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        ("+++ b/src/a.py\n+++ b/src/b.py\n", ("src/a.py", "src/b.py")),
        ("--- /dev/null\n+++ b/new.py\n", ("new.py",)),
        ("+++ /dev/null\n", ()),
        ("+++ b/a.py\n+++ b/a.py\n", ("a.py",)),
        ("", ()),
    ],
)
def test_changed_paths_are_read_from_the_destination_side(
    diff: str, expected: tuple[str, ...]
) -> None:
    assert changed_paths_from_diff(diff) == expected
