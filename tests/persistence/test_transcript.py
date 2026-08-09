"""The append-only log: its bytes, its durability, and its two failure modes."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from persistence_harness import (
    READ_RESULT,
    READ_USE,
    full_state,
    truncate_mid_line,
    two_turn_session,
)

from ronin.core.types import (
    AgentState,
    Budget,
    Message,
    Role,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.persistence import codec
from ronin.persistence.transcript import (
    SUFFIX,
    SessionMeta,
    Transcript,
    TranscriptError,
    list_sessions,
    meta_path,
    new_session_id,
    read_events,
    session_path,
    sessions_dir,
)

SESSION = "20260809-101112-abc123"


def write_session(directory: Path, session_id: str = SESSION, **kwargs: object) -> Path:
    events = two_turn_session()
    with Transcript.open(directory, session_id, **kwargs) as transcript:  # type: ignore[arg-type]
        transcript.extend(events)
        return transcript.path


# --------------------------------------------------------------------------- #
# Shape on disk
# --------------------------------------------------------------------------- #


def test_the_sessions_directory_is_dot_ronin_sessions(tmp_path: Path) -> None:
    assert sessions_dir(tmp_path) == tmp_path / ".ronin" / "sessions"
    assert session_path(tmp_path, SESSION).name == f"{SESSION}{SUFFIX}"


def test_every_event_is_one_line_of_compact_json(tmp_path: Path) -> None:
    """Read as bytes: ``read_text`` would translate line endings and hide a CRLF
    regression on the platform that has one."""
    path = write_session(sessions_dir(tmp_path))
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    lines = raw.split(b"\n")[:-1]
    assert len(lines) == len(two_turn_session()) + 1, "one header + one line per event"
    for line in lines:
        record = json.loads(line)
        assert isinstance(record, dict)
        assert record[codec.VERSION_KEY] == codec.SCHEMA_VERSION
    assert b"\n  " not in raw, "pretty-printing would break one-object-per-line"


def test_the_file_is_pure_ascii_so_a_torn_write_cannot_split_a_code_point(
    tmp_path: Path,
) -> None:
    directory = sessions_dir(tmp_path)
    with Transcript.open(directory, SESSION) as transcript:
        transcript.append(TextDelta(text="héllo ✓ \U0001f600"))
    raw = session_path(directory, SESSION).read_bytes()
    assert raw.decode("ascii")
    restored = read_events(session_path(directory, SESSION))
    assert restored.events == (TextDelta(text="héllo ✓ \U0001f600"),)


def test_the_first_line_is_the_session_header(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    write_session(directory, model="m-1", cwd="/work/repo", title="fix it")
    first = json.loads(session_path(directory, SESSION).read_bytes().split(b"\n")[0])
    assert first[codec.TYPE_KEY] == "session_header"
    read = read_events(session_path(directory, SESSION))
    assert read.header is not None
    assert (read.header.model, read.header.cwd, read.header.title) == (
        "m-1",
        "/work/repo",
        "fix it",
    )


def test_events_round_trip_through_a_real_file(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    path = write_session(directory)
    assert read_events(path).events == two_turn_session()


def test_an_empty_file_reads_as_nothing(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_bytes(b"")
    assert read_events(path).events == ()
    assert read_events(path).header is None


# --------------------------------------------------------------------------- #
# Durability
# --------------------------------------------------------------------------- #


def test_fsync_happens_at_turn_boundaries_and_not_per_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-event fsync is a device round trip per token: all cost, no benefit,
    because nobody resumes from half a streamed sentence."""
    calls: list[int] = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd) or real(fd))

    directory = sessions_dir(tmp_path)
    with Transcript.open(directory, SESSION) as transcript:
        after_open = len(calls)
        transcript.append(TurnStart(turn_index=0))
        for _ in range(50):
            transcript.append(TextDelta(text="chunk "))
        transcript.append(ToolStart(tool_use_id="t1", name="read"))
        assert len(calls) == after_open, "no fsync mid-turn"
        transcript.append(
            ToolEnd(tool_use_id="t1", name="read", result=ToolResult(ok=True))
        )
        assert len(calls) == after_open, "a ToolEnd is not a turn boundary"
        transcript.append(TurnEnd(turn_index=0, state=TurnState.DONE))
        assert len(calls) > after_open, "a TurnEnd must reach the device"


def test_close_syncs_even_when_the_last_turn_never_ended(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    transcript = Transcript.open(directory, SESSION)
    transcript.append(TurnStart(turn_index=0))
    transcript.close()
    transcript.close()  # idempotent
    assert read_events(transcript.path).events == (TurnStart(turn_index=0),)
    with pytest.raises(TranscriptError, match="closed"):
        transcript.append(TextDelta(text="late"))


def test_the_header_and_sidecar_land_before_any_event_can_be_written(
    tmp_path: Path,
) -> None:
    """A session that crashes in its first turn is still identifiable and listable."""
    directory = sessions_dir(tmp_path)
    transcript = Transcript.open(directory, SESSION, cwd="/work/repo")
    assert meta_path(directory, SESSION).exists()
    assert list_sessions(directory)[0].cwd == "/work/repo"
    transcript.close()


# --------------------------------------------------------------------------- #
# The two failure modes: a torn tail (recoverable) and a hole (not)
# --------------------------------------------------------------------------- #


def test_a_torn_final_line_is_reported_and_never_raises(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    path = write_session(directory)
    lost = truncate_mid_line(path)
    read = read_events(path)
    assert lost > 0
    assert read.truncated
    assert len(read.skipped) == 1
    assert "incomplete final record" in read.skipped[0]
    assert read.events == two_turn_session()[:-1]
    assert read.header is not None


def test_a_malformed_line_in_the_middle_refuses_to_load(tmp_path: Path) -> None:
    """Skipping it would hand replay a transcript with a hole in it."""
    directory = sessions_dir(tmp_path)
    path = write_session(directory)
    lines = path.read_bytes().split(b"\n")
    lines[3] = b'{"v":1,"t":"text_delta"'  # truncated, but terminated
    path.write_bytes(b"\n".join(lines))
    with pytest.raises(TranscriptError, match=r":4: malformed record in the middle"):
        read_events(path)


def test_an_unknown_record_type_in_the_middle_refuses_to_load(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    path = write_session(directory)
    lines = path.read_bytes().split(b"\n")
    lines[2] = json.dumps({"v": codec.SCHEMA_VERSION, "t": "from_the_future"}).encode()
    path.write_bytes(b"\n".join(lines))
    with pytest.raises(TranscriptError, match="from_the_future"):
        read_events(path)


def test_a_terminated_but_unreadable_final_line_is_also_dropped(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    path = write_session(directory)
    with path.open("ab") as handle:
        handle.write(b'{"v":1,"t":"text_del\n')
    read = read_events(path)
    assert read.events == two_turn_session()
    assert "unreadable final record" in read.skipped[0]


def test_reopening_a_torn_file_drops_the_torn_line_before_appending(
    tmp_path: Path,
) -> None:
    """Left in place, the torn line stops being last and becomes a hole — which
    would turn a recoverable crash into an unloadable session."""
    directory = sessions_dir(tmp_path)
    path = write_session(directory)
    truncate_mid_line(path)

    with Transcript.open(directory, SESSION) as reopened:
        assert reopened.repairs and "unterminated final line" in reopened.repairs[0]
        reopened.append(TurnStart(turn_index=2))
    read = read_events(path)
    assert read.skipped == ()
    assert read.events == (*two_turn_session()[:-1], TurnStart(turn_index=2))


def test_reopening_keeps_the_recorded_identity_and_counters(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    write_session(directory, model="m-1", cwd="/work/repo")
    with Transcript.open(directory, SESSION, model="ignored", cwd="/elsewhere") as again:
        assert (again.meta.model, again.meta.cwd) == ("m-1", "/work/repo")
        assert again.meta.turns == 1
        again.append(TurnEnd(turn_index=9, state=TurnState.DONE))
        assert again.meta.turns == 2


def test_a_blank_line_is_skipped_with_a_report(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    path = write_session(directory)
    raw = path.read_bytes().replace(b"\n", b"\n\n", 1)
    path.write_bytes(raw)
    read = read_events(path)
    assert read.events == two_turn_session()
    assert any("blank" in note for note in read.skipped)


def test_a_non_utf8_file_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "hand-edited.jsonl"
    path.write_bytes(b'{"v":1,"t":"text_delta","text":"\xff\xfe"}\n')
    with pytest.raises(TranscriptError, match="not valid UTF-8"):
        read_events(path)


# --------------------------------------------------------------------------- #
# Writes that must fail loudly rather than record something else
# --------------------------------------------------------------------------- #


def test_unserializable_tool_arguments_fail_loudly_and_write_nothing(
    tmp_path: Path,
) -> None:
    directory = sessions_dir(tmp_path)
    with Transcript.open(directory, SESSION) as transcript:
        before = transcript.path.read_bytes()
        with pytest.raises(TranscriptError, match="ToolStart"):
            transcript.append(
                ToolStart(tool_use_id="t1", name="read", arguments={"bad": {1, 2}})
            )
    assert read_events(directory / f"{SESSION}{SUFFIX}").events == ()
    assert before.count(b"\n") == 1


def test_a_nan_in_metadata_is_refused_rather_than_written_as_invalid_json(
    tmp_path: Path,
) -> None:
    """``NaN`` is not JSON; a writer that emits it produces a file no reader can
    load, which is the one thing an append-only log must never do."""
    directory = sessions_dir(tmp_path)
    state = full_state()
    poisoned = state.with_message(
        Message(role=Role.USER, metadata={"ratio": float("nan")})
    )
    with Transcript.open(directory, SESSION) as transcript:
        with pytest.raises(TranscriptError, match="TurnEnd"):
            transcript.append(
                TurnEnd(turn_index=0, state=TurnState.DONE, agent_state=poisoned)
            )


# --------------------------------------------------------------------------- #
# Metadata and the picker
# --------------------------------------------------------------------------- #


def test_metadata_accumulates_cost_files_and_checkpoints(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    with Transcript.open(directory, SESSION, model="m-1", cwd="/work/repo") as transcript:
        transcript.append(TurnStart(turn_index=0))
        transcript.append(ToolStart(tool_use_id=READ_USE.id, name="read"))
        transcript.append(
            ToolEnd(tool_use_id=READ_USE.id, name="read", result=READ_RESULT)
        )
        transcript.note_files(["docs/NOTES.md"])
        transcript.append(
            TurnEnd(
                turn_index=0,
                state=TurnState.DONE,
                agent_state=full_state(),
            )
        )
        meta = transcript.meta
    assert meta.turns == 1
    assert meta.events == 4
    assert meta.cost_usd == full_state().budget.spent_usd
    assert meta.checkpoint_ids == ("ckpt-9",)
    assert meta.files_touched == ("src/app.py", "docs/NOTES.md")
    assert meta.duration_seconds >= 0.0


def test_list_sessions_does_not_read_the_events(tmp_path: Path) -> None:
    """The picker's cost must not scale with transcript size. Proven behaviourally:
    the log's body is replaced with garbage that ``read_events`` refuses, and the
    picker still returns a correct row."""
    directory = sessions_dir(tmp_path)
    path = write_session(directory, model="m-1", cwd="/work/repo")
    header = path.read_bytes().split(b"\n")[0]
    path.write_bytes(header + b"\n" + b"not json at all\n" * 500)

    rows = list_sessions(directory)
    assert [row.session_id for row in rows] == [SESSION]
    assert rows[0].model == "m-1" and rows[0].turns == 1
    with pytest.raises(TranscriptError):
        read_events(path)


def test_the_picker_falls_back_to_the_header_and_says_the_row_is_stale(
    tmp_path: Path,
) -> None:
    directory = sessions_dir(tmp_path)
    write_session(directory, model="m-1", cwd="/work/repo")
    meta_path(directory, SESSION).unlink()
    row = list_sessions(directory)[0]
    assert row.stale is True
    assert row.model == "m-1"
    assert row.turns == 0, "the header cannot know about later turns, and says so"


def test_sessions_are_listed_newest_first(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    clock = iter([100.0, 100.0, 200.0, 200.0, 300.0, 300.0, 400.0, 400.0])
    for session_id in ("aaa", "bbb", "ccc"):
        with Transcript.open(
            directory, session_id, clock=lambda: next(clock)
        ) as transcript:
            transcript.append(TurnEnd(turn_index=0, state=TurnState.DONE))
    assert [row.session_id for row in list_sessions(directory)] == ["ccc", "bbb", "aaa"]


def test_list_sessions_on_a_missing_directory_is_empty_not_an_error(
    tmp_path: Path,
) -> None:
    assert list_sessions(tmp_path / "nope") == ()


def test_a_corrupt_sidecar_falls_back_to_the_header(tmp_path: Path) -> None:
    directory = sessions_dir(tmp_path)
    write_session(directory, model="m-1")
    meta_path(directory, SESSION).write_text("{ this is not json", encoding="utf-8")
    row = list_sessions(directory)[0]
    assert row.stale is True and row.model == "m-1"


def test_session_metadata_round_trips(tmp_path: Path) -> None:
    meta = SessionMeta(
        session_id=SESSION,
        cwd="/work/repo",
        model="m-1",
        title="t",
        started_at=1.5,
        updated_at=2.5,
        turns=3,
        events=40,
        cost_usd=0.25,
        duration_seconds=1.0,
        files_touched=("a", "b"),
        checkpoint_ids=("c1",),
    )
    from ronin.persistence.transcript import decode_meta, encode_meta

    assert decode_meta(json.loads(json.dumps(encode_meta(meta)))) == meta


def test_a_session_id_is_time_ordered_with_entropy() -> None:
    generated = new_session_id(clock=lambda: 0.0, entropy=lambda n: "ab" * n)
    assert generated.endswith("-ababab")
    assert len(generated.split("-")) == 3


def test_the_sidecar_is_never_ahead_of_the_log(tmp_path: Path) -> None:
    """The sidecar is a cache of the log, so it may lag by a turn but must never
    describe a turn that is not on disk."""
    directory = sessions_dir(tmp_path)
    with Transcript.open(directory, SESSION) as transcript:
        transcript.append(TurnStart(turn_index=0))
        transcript.append(
            TurnEnd(
                turn_index=0,
                state=TurnState.DONE,
                agent_state=AgentState(budget=Budget(spent_usd=0.5)),
            )
        )
        sidecar = json.loads(meta_path(directory, SESSION).read_text(encoding="utf-8"))
        assert sidecar["turns"] == 1
        assert sidecar["cost_usd"] == 0.5
        assert len(read_events(transcript.path).events) == 2
