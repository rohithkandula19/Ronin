"""The four verdicts, and the two details that would silently break the guard.

The CRLF test is the one worth reading: a tracker that hashes decoded text calls a
whole-file line-ending rewrite "unchanged", and the model then clobbers it.

The second half covers what the module grew later — the window, the completeness
flag, and ``retain_only`` — which had no tests in its own test file at all. That
gap mattered more than it looks: those three exist to keep a record from claiming
more than the model was shown, so an untested one is a guard that can be wrong in
the direction nobody notices. They were exercised only through the gate, which
tests the wiring rather than the rules.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ronin.context.filestate import (
    WHOLE_FILE,
    FileStateTracker,
    FileStatus,
    ReadWindow,
    digest_bytes,
)


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_a_file_read_and_left_alone_is_unchanged(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"print(1)\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    check = tracker.check(path)
    assert check.status is FileStatus.UNCHANGED
    assert check.safe_to_edit
    assert not check.must_reread


def test_an_external_edit_is_reported_as_changed_with_a_reread_instruction(
    tmp_path: Path,
) -> None:
    path = write(tmp_path / "a.py", b"print(1)\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    write(path, b"print(2)\n")
    check = tracker.check(path)
    assert check.status is FileStatus.CHANGED
    assert not check.safe_to_edit
    assert check.must_reread
    assert "read" in check.message and str(path) in check.message
    assert check.recorded_digest != check.current_digest


def test_a_deleted_file_is_reported_as_deleted_and_not_as_changed(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"x\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    path.unlink()
    check = tracker.check(path)
    assert check.status is FileStatus.DELETED
    assert "no longer exists" in check.message
    assert check.current_digest == ""


def test_a_file_never_read_says_so_rather_than_guessing(tmp_path: Path) -> None:
    check = FileStateTracker().check(tmp_path / "missing.py")
    assert check.status is FileStatus.NEVER_READ
    assert check.must_reread
    assert "has not been read" in check.message


def test_every_status_has_a_distinct_actionable_message(tmp_path: Path) -> None:
    messages = set()
    tracker = FileStateTracker()
    unchanged = write(tmp_path / "u.py", b"a\n")
    changed = write(tmp_path / "c.py", b"a\n")
    deleted = write(tmp_path / "d.py", b"a\n")
    tracker.record_read(unchanged)
    tracker.record_read(changed)
    tracker.record_read(deleted)
    write(changed, b"b\n")
    deleted.unlink()
    for path in (unchanged, changed, deleted, tmp_path / "n.py"):
        message = tracker.check(path).message
        assert str(path) in message
        messages.add(message.replace(str(path), "<path>"))
    assert len(messages) == 4


def test_a_line_ending_rewrite_is_caught_because_the_hash_is_over_bytes(
    tmp_path: Path,
) -> None:
    """`Path.read_text()` would translate CRLF to LF and call this unchanged."""
    path = write(tmp_path / "a.py", b"one\ntwo\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    write(path, b"one\r\ntwo\r\n")
    assert tracker.check(path).status is FileStatus.CHANGED


def test_a_touched_but_identical_file_is_unchanged(tmp_path: Path) -> None:
    """mtime is a fast path, not the answer: `touch` must not force a re-read."""
    path = write(tmp_path / "a.py", b"same\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns + 10**9, stat.st_mtime_ns + 10**9))
    assert tracker.check(path).status is FileStatus.UNCHANGED


def test_a_same_size_different_content_edit_is_caught(tmp_path: Path) -> None:
    """The case a size-only comparison misses entirely."""
    path = write(tmp_path / "a.py", b"aaaa\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    write(path, b"bbbb\n")
    assert tracker.check(path).status is FileStatus.CHANGED


def test_recording_the_bytes_shown_beats_re_reading_them(tmp_path: Path) -> None:
    """The digest must describe what the model saw, not a later re-read."""
    path = write(tmp_path / "a.py", b"shown\n")
    tracker = FileStateTracker()
    record = tracker.record_read(path, data=b"shown\n")
    assert record.digest == digest_bytes(b"shown\n")
    write(path, b"raced\n")
    assert tracker.check(path).status is FileStatus.CHANGED


def test_re_reading_after_a_change_makes_the_file_editable_again(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"one\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    write(path, b"two\n")
    assert tracker.check(path).status is FileStatus.CHANGED
    tracker.record_read(path)
    assert tracker.check(path).status is FileStatus.UNCHANGED


def test_dropping_a_record_puts_the_file_back_to_never_read(tmp_path: Path) -> None:
    """``retain_only`` is the only way a record leaves, and it must leave completely.

    Half-forgetting is the dangerous outcome: a path still in ``known_paths`` reports
    a verdict, and any verdict but ``NEVER_READ`` is an invitation to edit blind.
    """
    path = write(tmp_path / "a.py", b"x\n")
    tracker = FileStateTracker()
    tracker.record_read(path)

    assert tracker.retain_only(()) == (str(path),)

    assert tracker.check(path).status is FileStatus.NEVER_READ
    assert tracker.known_paths() == ()
    assert tracker.recorded(path) is None


def test_known_paths_and_check_all_are_sorted_for_reproducible_output(
    tmp_path: Path,
) -> None:
    tracker = FileStateTracker()
    for name in ("z.py", "a.py", "m.py"):
        tracker.record_read(write(tmp_path / name, b"x\n"))
    assert list(tracker.known_paths()) == sorted(tracker.known_paths())
    assert [check.path for check in tracker.check_all()] == sorted(tracker.known_paths())


def test_check_all_gives_each_file_its_own_verdict(tmp_path: Path) -> None:
    """One sweep, three different answers — the point of the plural.

    A sweep that collapsed to a single verdict, or that let one file's status leak
    into the next, would be worse than useless: the caller would act on it.
    """
    tracker = FileStateTracker()
    stable = write(tmp_path / "stable.py", b"x\n")
    moved = write(tmp_path / "moved.py", b"x\n")
    gone = write(tmp_path / "gone.py", b"x\n")
    for path in (stable, moved, gone):
        tracker.record_read(path)
    write(moved, b"y\n")
    gone.unlink()

    verdicts = {check.path: check.status for check in tracker.check_all()}

    assert verdicts == {
        str(stable): FileStatus.UNCHANGED,
        str(moved): FileStatus.CHANGED,
        str(gone): FileStatus.DELETED,
    }


def test_an_injected_reader_is_used_instead_of_the_filesystem(tmp_path: Path) -> None:
    """The seam a test uses to stage a race without racing."""
    path = write(tmp_path / "a.py", b"on disk\n")
    served: list[bytes] = [b"first\n", b"second\n"]

    def reader(_: Path) -> bytes:
        return served.pop(0)

    tracker = FileStateTracker(read_bytes=reader)
    tracker.record_read(path)
    write(path, b"forces a stat mismatch\n")
    assert tracker.check(path).status is FileStatus.CHANGED


def test_a_reader_that_raises_oserror_reports_deleted(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"x\n")

    def reader(_: Path) -> bytes:
        raise OSError("vanished")

    tracker = FileStateTracker()
    tracker.record_read(path)
    tracker.read_bytes = reader
    write(path, b"different size\n")
    assert tracker.check(path).status is FileStatus.DELETED


def test_reading_a_missing_file_raises_rather_than_recording_a_lie(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        FileStateTracker().record_read(tmp_path / "nope.py")


# --------------------------------------------------------------------------- #
# the window: two reads of one file are interchangeable only if they asked alike
# --------------------------------------------------------------------------- #


def test_a_bare_read_and_an_explicit_first_line_are_the_same_window() -> None:
    # Otherwise the two spellings of "from the top" are two records that never agree.
    assert ReadWindow.of() == ReadWindow.of(1) == WHOLE_FILE


def test_a_non_positive_limit_is_dropped_rather_than_carried() -> None:
    # `limit=0` is not "zero lines" to the read tool — it slices nothing and serves
    # the default. Carried through, it renders as "lines 1-0".
    assert ReadWindow.of(None, 0) == WHOLE_FILE
    assert ReadWindow.of(None, -5) == WHOLE_FILE
    assert ReadWindow.of(5, 0) == ReadWindow(offset=5, limit=None)


def test_offset_zero_is_not_a_line_number() -> None:
    assert ReadWindow.of(0) == WHOLE_FILE


def test_a_window_is_recorded_as_asked_for(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"\n".join(b"line %d" % i for i in range(1, 200)))
    tracker = FileStateTracker()

    record = tracker.record_read(path, window=ReadWindow.of(100, 20))

    assert record.window == ReadWindow(offset=100, limit=20)


def test_the_digest_stays_whole_file_even_for_a_windowed_read(tmp_path: Path) -> None:
    """Deliberate, and the reason the window is a separate field.

    A digest of twenty lines would call the file unchanged after an edit five hundred
    lines away. The digest answers "did the file move"; the window answers "what did
    the model see". Conflating them loses one answer or the other.
    """
    body = b"\n".join(b"line %d" % i for i in range(1, 200))
    path = write(tmp_path / "a.py", body)
    tracker = FileStateTracker()

    record = tracker.record_read(path, body, window=ReadWindow.of(100, 20))

    assert record.digest == digest_bytes(body)


# --------------------------------------------------------------------------- #
# satisfies: the whole condition for answering a read without re-sending it
# --------------------------------------------------------------------------- #


def test_a_matching_window_on_an_unchanged_file_is_satisfied(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"x = 1\n")
    tracker = FileStateTracker()
    tracker.record_read(path)

    assert tracker.satisfies(path, WHOLE_FILE)


def test_a_different_window_is_not_satisfied(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"\n".join(b"line %d" % i for i in range(1, 200)))
    tracker = FileStateTracker()
    tracker.record_read(path, window=ReadWindow.of(100, 20))

    assert tracker.satisfies(path, ReadWindow.of(100, 20))
    assert not tracker.satisfies(path, WHOLE_FILE)
    assert not tracker.satisfies(path, ReadWindow.of(1, 20))


def test_a_changed_file_is_not_satisfied(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"x = 1\n")
    tracker = FileStateTracker()
    tracker.record_read(path)

    write(path, b"x = 2\n")

    assert not tracker.satisfies(path, WHOLE_FILE)


def test_a_file_never_read_is_not_satisfied(tmp_path: Path) -> None:
    assert not FileStateTracker().satisfies(tmp_path / "never.py", WHOLE_FILE)


# --------------------------------------------------------------------------- #
# complete: a sound baseline is not the same as a stand-in for content
# --------------------------------------------------------------------------- #


def test_a_record_is_complete_unless_told_otherwise(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"x = 1\n")
    assert FileStateTracker().record_read(path).complete


def test_an_incomplete_record_still_detects_change_but_never_satisfies(
    tmp_path: Path,
) -> None:
    """The distinction the flag exists to draw.

    A capped or clamped read showed the model a prefix. That is still a perfectly
    good answer to "did the file move" — and no answer at all to "does the model
    hold this file".
    """
    path = write(tmp_path / "a.py", b"x = 1\n")
    tracker = FileStateTracker()
    tracker.record_read(path, complete=False)

    assert tracker.check(path).status is FileStatus.UNCHANGED
    assert not tracker.satisfies(path, WHOLE_FILE)

    write(path, b"x = 2\n")
    assert tracker.check(path).status is FileStatus.CHANGED


def test_mark_incomplete_downgrades_a_record_in_place(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"x = 1\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    assert tracker.satisfies(path, WHOLE_FILE)

    tracker.mark_incomplete(path)

    assert not tracker.satisfies(path, WHOLE_FILE)
    assert tracker.check(path).status is FileStatus.UNCHANGED  # baseline survives


def test_mark_incomplete_on_an_unknown_path_is_a_no_op(tmp_path: Path) -> None:
    tracker = FileStateTracker()
    tracker.mark_incomplete(tmp_path / "never.py")  # must not raise or invent a record
    assert tracker.known_paths() == ()


# --------------------------------------------------------------------------- #
# retain_only: forgetting is how the guard stops describing a vanished transcript
# --------------------------------------------------------------------------- #


def test_retain_only_drops_what_is_not_named_and_reports_it(tmp_path: Path) -> None:
    kept = write(tmp_path / "kept.py", b"a = 1\n")
    gone = write(tmp_path / "gone.py", b"b = 2\n")
    tracker = FileStateTracker()
    tracker.record_read(kept)
    tracker.record_read(gone)

    dropped = tracker.retain_only([str(kept)])

    assert dropped == (str(gone),)
    assert tracker.known_paths() == (str(kept),)


def test_retain_only_reports_in_a_stable_order(tmp_path: Path) -> None:
    # The caller prints these; unsorted output would churn between runs.
    tracker = FileStateTracker()
    for name in ("c.py", "a.py", "b.py"):
        tracker.record_read(write(tmp_path / name, b"x\n"))

    assert tracker.retain_only(()) == tuple(
        sorted(str(tmp_path / name) for name in ("a.py", "b.py", "c.py"))
    )


def test_a_forgotten_file_reads_as_never_read_not_as_unchanged(tmp_path: Path) -> None:
    """The point of forgetting. NEVER_READ advises; UNCHANGED would assert something
    false about content the model no longer holds."""
    path = write(tmp_path / "a.py", b"x = 1\n")
    tracker = FileStateTracker()
    tracker.record_read(path)

    tracker.retain_only(())

    check = tracker.check(path)
    assert check.status is FileStatus.NEVER_READ
    assert not check.safe_to_edit
    assert "Read it first" in check.message


def test_retaining_everything_drops_nothing(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"x = 1\n")
    tracker = FileStateTracker()
    tracker.record_read(path)

    assert tracker.retain_only([str(path)]) == ()
    assert tracker.recorded(path) is not None
