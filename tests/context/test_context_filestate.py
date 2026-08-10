"""The four verdicts, and the two details that would silently break the guard.

The CRLF test is the one worth reading: a tracker that hashes decoded text calls a
whole-file line-ending rewrite "unchanged", and the model then clobbers it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ronin.context.filestate import (
    FileStateTracker,
    FileStatus,
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


def test_forget_drops_the_record(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", b"x\n")
    tracker = FileStateTracker()
    tracker.record_read(path)
    tracker.forget(path)
    assert tracker.check(path).status is FileStatus.NEVER_READ
    assert tracker.known_paths() == ()


def test_known_paths_and_check_all_are_sorted_for_reproducible_output(
    tmp_path: Path,
) -> None:
    tracker = FileStateTracker()
    for name in ("z.py", "a.py", "m.py"):
        tracker.record_read(write(tmp_path / name, b"x\n"))
    assert list(tracker.known_paths()) == sorted(tracker.known_paths())
    assert [check.path for check in tracker.check_all()] == sorted(tracker.known_paths())


def test_changed_since_read_lists_only_what_moved(tmp_path: Path) -> None:
    tracker = FileStateTracker()
    stable = write(tmp_path / "stable.py", b"x\n")
    moved = write(tmp_path / "moved.py", b"x\n")
    gone = write(tmp_path / "gone.py", b"x\n")
    for path in (stable, moved, gone):
        tracker.record_read(path)
    write(moved, b"y\n")
    gone.unlink()
    assert tracker.changed_since_read() == (str(gone), str(moved))


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
