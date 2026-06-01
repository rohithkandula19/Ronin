"""Tests for the conversation archive — every session saved & resumable."""
from __future__ import annotations

from pathlib import Path

import pytest

from ro_claude_kit_cli import sessions


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # sessions live under ./.csk relative to cwd — chdir so tests don't pollute
    # the repo, and reset the per-process session id between tests.
    monkeypatch.chdir(tmp_path)
    sessions._session_id = None
    yield


def test_every_session_is_archived_not_overwritten(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    sessions.save_session(proj, ["USER: first q", "ASSISTANT: a1"], session_id="20260101-000001-aaaaaa")
    sessions.save_session(proj, ["USER: second q", "ASSISTANT: a2"], session_id="20260101-000002-bbbbbb")
    files = list((tmp_path / ".csk" / "sessions").glob("*.json"))
    assert len(files) == 2   # two distinct files — kept, not overwritten


def test_list_is_recent_first_and_filtered_by_project(tmp_path: Path) -> None:
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    sessions.save_session(a, ["USER: in A"], session_id="20260101-000001-aaaaaa")
    sessions.save_session(b, ["USER: in B"], session_id="20260101-000002-bbbbbb")
    all_s = sessions.list_sessions()
    assert len(all_s) == 2
    assert all_s[0]["id"] == "20260101-000002-bbbbbb"   # most recent first
    just_a = sessions.list_sessions(a)
    assert len(just_a) == 1 and just_a[0]["title"] == "in A"


def test_load_by_id_and_latest(tmp_path: Path) -> None:
    proj = tmp_path / "p"; proj.mkdir()
    sessions.save_session(proj, ["USER: q1", "ASSISTANT: a1"], session_id="20260101-000001-aaaaaa")
    sessions.save_session(proj, ["USER: q2", "ASSISTANT: a2"], session_id="20260101-000002-bbbbbb")
    assert sessions.latest_session(proj) == "20260101-000002-bbbbbb"
    assert sessions.load_session("20260101-000001-aaaaaa") == ["USER: q1", "ASSISTANT: a1"]
    assert sessions.load_session("nope") == []


def test_continue_writes_back_to_same_file(tmp_path: Path) -> None:
    proj = tmp_path / "p"; proj.mkdir()
    sessions.save_session(proj, ["USER: q1"], session_id="20260101-000001-aaaaaa")
    sessions.set_current_session("20260101-000001-aaaaaa")   # continue it
    sessions.save_session(proj, ["USER: q1", "ASSISTANT: a1", "USER: q2"])   # no explicit id → uses current
    files = list((tmp_path / ".csk" / "sessions").glob("*.json"))
    assert len(files) == 1                                   # same file, not a new one
    assert sessions.load_session("20260101-000001-aaaaaa")[-1] == "USER: q2"


def test_metadata_title_and_turn_count(tmp_path: Path) -> None:
    sessions.save_session(tmp_path, ["USER: fix the bug", "ASSISTANT: ok", "USER: now test it"],
                          session_id="20260101-000001-aaaaaa")
    meta = sessions.list_sessions(tmp_path)[0]
    assert meta["title"] == "fix the bug"
    assert meta["turns"] == 2


def test_empty_transcript_not_saved(tmp_path: Path) -> None:
    assert sessions.save_session(tmp_path, []) is None
    assert sessions.list_sessions() == []
