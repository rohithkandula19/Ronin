"""A transcript from another schema version, and an id that is not a filename.

Two failures that share a shape: the layer had a precise, well-reasoned answer and no
path for a user to ever receive it.

**Version mismatch was reported as corruption.** `SchemaVersionMismatch` says exactly
what happened and what to do about it — *export the old session with the Ronin that
wrote it* — and `read_events` caught it alongside every other decode failure and
re-raised it as "malformed record in the middle of the transcript ... refusing to load a
transcript with a hole in it". The file has no hole. Meanwhile `list_sessions` dropped
the row entirely, so the session the user was looking for simply was not listed. Both
land at the same moment — the first schema bump, when *every* session a user owns is the
old version — which is the worst possible time to tell them their files are broken and
then hide them.

**A session id became a filename with nothing checking it.** Ids do not all come from
`new_session_id`: `--resume <id>` and `--export-session <id>` take one from argv and the
SDK takes one from its caller. `session_path(dir, "../../x")` named a file outside
`.ronin/sessions`, and on the writing side created one. Every other layer confines paths;
this one did not.

The tests below are paired throughout: the refusal *and* the thing that must still work,
because a validator that rejects everything would pass every negative test here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from persistence_harness import two_turn_session

from ronin.persistence.codec import SCHEMA_VERSION, VERSION_KEY, SchemaVersionMismatch
from ronin.persistence.resume import resume_session
from ronin.persistence.transcript import (
    SessionMeta,
    Transcript,
    TranscriptError,
    TranscriptVersionError,
    list_sessions,
    meta_path,
    new_session_id,
    read_events,
    session_path,
    valid_session_id,
)

SESSION_ID = "20260101-000000-abcdef"


def write_session(directory: Path, session_id: str = SESSION_ID) -> Path:
    """A real, well-formed session on disk, written by the real writer."""
    with Transcript.open(directory, session_id, cwd=str(directory), model="m") as transcript:
        transcript.extend(two_turn_session())
    return session_path(directory, session_id)


def bump_version(path: Path, version: int = SCHEMA_VERSION + 1) -> None:
    """Rewrite every record's version field and change nothing else.

    The point of doing it this way: the file stays byte-for-byte legal JSONL with legal
    records, so a test that passes cannot be passing because the file is malformed. The
    *only* difference from a session this build wrote is the version number.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        record[VERSION_KEY] = version
        out.append(json.dumps(record, separators=(",", ":")))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def future_session(directory: Path, session_id: str = SESSION_ID) -> Path:
    """A complete session, then bumped — sidecar included, as an upgrade would leave it."""
    path = write_session(directory, session_id)
    bump_version(path)
    sidecar = meta_path(directory, session_id)
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    record[VERSION_KEY] = SCHEMA_VERSION + 1
    sidecar.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# read_events: a version mismatch is not corruption
# --------------------------------------------------------------------------- #


def test_a_future_version_is_refused_as_a_version_problem_not_a_hole(tmp_path: Path) -> None:
    """The message decides where the user looks next. "Malformed record in the middle"
    sends them hunting for a broken file; the version text tells them the file is fine
    and which Ronin can read it."""
    path = future_session(tmp_path)
    with pytest.raises(TranscriptVersionError) as caught:
        read_events(path)

    message = str(caught.value)
    assert "schema version" in message
    assert f"version {SCHEMA_VERSION}" in message
    assert "export the old session with the Ronin that wrote it" in message
    assert "hole" not in message, "a version mismatch is not corruption"
    assert "malformed" not in message


def test_the_version_error_names_the_file_and_the_line(tmp_path: Path) -> None:
    """Same as every other transcript refusal: a path and a line number, because the
    next thing a maintainer does is open the file."""
    path = future_session(tmp_path)
    with pytest.raises(TranscriptVersionError, match=r":1:"):
        read_events(path)


def test_the_version_error_is_catchable_as_either_thing_it_is(tmp_path: Path) -> None:
    """Both bases carry weight, and dropping either one breaks a real caller.

    `TranscriptError` is a `RuntimeError` and the CLI's resume path catches
    `(OSError, RuntimeError)` — a bare `SchemaVersionMismatch` (a `ValueError`) would
    have reached the user as a traceback. And a caller that wants to distinguish
    "another Ronin wrote this" from "this is corrupt" needs the codec's type, because
    only one of those two is recoverable.
    """
    path = future_session(tmp_path)
    with pytest.raises(TranscriptError):
        read_events(path)
    with pytest.raises(SchemaVersionMismatch):
        read_events(path)
    with pytest.raises(RuntimeError):
        read_events(path)


def test_a_version_mismatch_on_the_final_line_still_raises(tmp_path: Path) -> None:
    """A torn *final* line is tolerated; a version mismatch on one is not.

    One build writes one transcript, so a bad version on the last line is a fact about
    the whole file rather than about how it ended. Tolerating it there would load a
    session's history minus its most recent turn and call that a successful read.
    """
    path = write_session(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    record[VERSION_KEY] = SCHEMA_VERSION + 1
    lines[-1] = json.dumps(record, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(TranscriptVersionError):
        read_events(path)


def test_a_session_this_build_wrote_still_loads(tmp_path: Path) -> None:
    """The control. A version check that refused everything would pass all of the
    above."""
    result = read_events(write_session(tmp_path))
    assert result.events
    assert result.header is not None
    assert result.skipped == ()


# --------------------------------------------------------------------------- #
# the picker lists it rather than hiding it
# --------------------------------------------------------------------------- #


def test_a_future_version_session_is_listed_with_a_reason(tmp_path: Path) -> None:
    """It was invisible before. A user upgrading Ronin would run `ronin sessions`, see
    nothing, and have no way to learn that the sessions were still on disk."""
    future_session(tmp_path)
    rows = list_sessions(tmp_path)

    assert [row.session_id for row in rows] == [SESSION_ID]
    assert not rows[0].loadable
    assert "schema version" in rows[0].unreadable


def test_an_unreadable_row_does_not_claim_counters_it_cannot_know(tmp_path: Path) -> None:
    """Zeroes would read as "an empty session", which is a different and wrong story.
    The row carries identity and the reason, and stays quiet about the rest."""
    future_session(tmp_path)
    row = list_sessions(tmp_path)[0]

    assert (row.turns, row.events, row.cost_usd) == (0, 0, 0.0)
    assert row.unreadable, "the reason is the row's only claim beyond its id"


def test_a_readable_session_is_still_marked_loadable(tmp_path: Path) -> None:
    """The control, and the property every existing caller depends on."""
    write_session(tmp_path)
    row = list_sessions(tmp_path)[0]
    assert row.loadable
    assert row.unreadable == ""
    assert row.turns == 1


def test_a_readable_session_and_an_unreadable_one_both_appear(tmp_path: Path) -> None:
    """Mixed is the realistic state after an upgrade: new sessions alongside old ones.
    One unreadable file must not cost the readable rows."""
    write_session(tmp_path, "20260301-120000-newone")
    future_session(tmp_path, "20260101-000000-oldone")

    rows = {row.session_id: row.loadable for row in list_sessions(tmp_path)}
    assert rows == {"20260301-120000-newone": True, "20260101-000000-oldone": False}


def test_a_stale_sidecar_from_another_version_does_not_condemn_a_readable_log(
    tmp_path: Path,
) -> None:
    """The sidecar is a *cache*. If it is from another version but the log's own header
    is ours, the session is readable and the row must say so — the log is the truth and
    the sidecar is the thing that can be thrown away."""
    write_session(tmp_path)
    sidecar = meta_path(tmp_path, SESSION_ID)
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    record[VERSION_KEY] = SCHEMA_VERSION + 1
    sidecar.write_text(json.dumps(record) + "\n", encoding="utf-8")

    row = list_sessions(tmp_path)[0]
    assert row.loadable, row.unreadable
    assert row.stale, "the counters came from the header, so the row says so"


def test_resuming_an_unreadable_session_says_why(tmp_path: Path) -> None:
    """The end-to-end path: a user who *does* name the session gets the actionable
    message rather than a corruption report."""
    future_session(tmp_path)
    with pytest.raises(TranscriptVersionError, match="schema version"):
        resume_session(tmp_path, SESSION_ID)


# --------------------------------------------------------------------------- #
# appending to a version we cannot read
# --------------------------------------------------------------------------- #


def test_opening_a_future_version_log_for_append_is_refused(tmp_path: Path) -> None:
    """Appending would interleave two builds' records and produce a file neither can
    read — turning a session that one Ronin could still export into one that nothing
    can. Refusing keeps the old file intact and recoverable."""
    path = future_session(tmp_path)
    before = path.read_bytes()

    with pytest.raises(TranscriptVersionError, match="cannot append"):
        Transcript.open(tmp_path, SESSION_ID, cwd=str(tmp_path))

    assert path.read_bytes() == before, "the refusal must not have written anything"


def test_reopening_a_session_this_build_wrote_still_appends(tmp_path: Path) -> None:
    """The control: the refusal above must be about the version and nothing else."""
    write_session(tmp_path)
    with Transcript.open(tmp_path, SESSION_ID, cwd=str(tmp_path)) as transcript:
        assert transcript.meta.turns == 1
    assert read_events(session_path(tmp_path, SESSION_ID)).events


# --------------------------------------------------------------------------- #
# a damaged header must not gain a second one
# --------------------------------------------------------------------------- #


def test_reopening_a_log_with_an_unreadable_header_does_not_append_a_second(
    tmp_path: Path,
) -> None:
    """A header belongs at the top of a file.

    "Fresh" was decided by *could we read a header*, so a log whose first line was
    damaged got a brand-new header appended below its events — a header record in the
    middle of the file, which is the one thing `read_events` refuses to load. One bad
    line became an unloadable session.
    """
    path = write_session(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = '{"v":1,"t":"session_header","cwd":"/gone"}'  # no session_id: undecodable
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta_path(tmp_path, SESSION_ID).unlink()

    with Transcript.open(tmp_path, SESSION_ID, cwd=str(tmp_path)) as transcript:
        transcript.note_files(["src/app.py"])

    headers = [
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines())
        if json.loads(line).get("t") == "session_header"
    ]
    assert headers == [0], f"a header record appeared at {headers}"


def test_a_fresh_session_still_gets_its_header_first(tmp_path: Path) -> None:
    """The control. The header is what makes a session identifiable after a crash in
    its first turn, so it must still land before any event."""
    with Transcript.open(tmp_path, "brandnew", cwd=str(tmp_path)):
        pass
    first = json.loads(session_path(tmp_path, "brandnew").read_text().splitlines()[0])
    assert first["t"] == "session_header"
    assert first["session_id"] == "brandnew"


# --------------------------------------------------------------------------- #
# a session id is a filename, and is checked like one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "session_id",
    ["../escaped", "../../../etc/passwd", "..", ".", "", "a/b", "a\\b", "sub/../x", "a b", "x\ty"],
    ids=[
        "parent",
        "deep-traversal",
        "dotdot",
        "dot",
        "empty",
        "slash",
        "backslash",
        "traversal-inside",
        "space",
        "tab",
    ],
)
def test_an_id_that_is_not_a_bare_filename_is_refused(tmp_path: Path, session_id: str) -> None:
    """`--resume` and `--export-session` take an id from argv and the SDK from its
    caller, so an id is untrusted input that becomes a path. Refused at the one place
    that turns it into one, so every caller inherits the check."""
    assert not valid_session_id(session_id)
    with pytest.raises(TranscriptError, match="not a usable session id"):
        session_path(tmp_path, session_id)
    with pytest.raises(TranscriptError):
        meta_path(tmp_path, session_id)


def test_a_traversing_id_cannot_create_a_file_outside_the_sessions_directory(
    tmp_path: Path,
) -> None:
    """The writing side, which is the half that does damage. Asserted by looking at the
    filesystem rather than at the exception, because the guarantee is about what is on
    disk."""
    directory = tmp_path / "root" / ".ronin" / "sessions"
    directory.mkdir(parents=True)
    escaped = tmp_path / "root" / "escaped.jsonl"

    with pytest.raises(TranscriptError):
        Transcript.open(directory, "../escaped", cwd=str(tmp_path))

    assert not escaped.exists()
    assert list(directory.iterdir()) == []


def test_resume_refuses_a_traversing_id_before_it_reads_anything(tmp_path: Path) -> None:
    """The reading side. `ronin --resume ../../../etc/passwd` must not open that file
    even to fail on its contents."""
    with pytest.raises(TranscriptError, match="not a usable session id"):
        resume_session(tmp_path, "../../../etc/passwd")


@pytest.mark.parametrize(
    "session_id",
    ["demo", "s1", "m-1", "20260101-000000-abcdef", "a.b_c-1", "UPPER", "9"],
    ids=["demo", "short", "dashed", "generated", "mixed-punctuation", "upper", "digit"],
)
def test_an_ordinary_id_is_still_accepted(session_id: str) -> None:
    """The control, and it is load-bearing: `demo` and the generated form are what the
    demos and the CLI actually use, so an over-strict rule would break the product
    rather than protect it."""
    assert valid_session_id(session_id)


def test_the_generated_id_satisfies_the_rule_it_is_checked_against() -> None:
    """The two must not be able to drift: a `new_session_id` that stopped passing
    `valid_session_id` would make Ronin unable to open the sessions it just named."""
    assert valid_session_id(new_session_id())


def test_a_foreign_filename_in_the_sessions_directory_is_skipped_not_fatal(
    tmp_path: Path,
) -> None:
    """`list_sessions` derives an id from a filename, so a file someone else dropped in
    there is a name the rule rejects. One odd file must not cost the whole picker —
    which is the same reason the version row is listed rather than raised."""
    write_session(tmp_path)
    (tmp_path / "not a session.jsonl").write_text("{}\n", encoding="utf-8")

    assert [row.session_id for row in list_sessions(tmp_path)] == [SESSION_ID]


# --------------------------------------------------------------------------- #
# SessionMeta's own rules
# --------------------------------------------------------------------------- #


def test_a_session_meta_needs_an_id() -> None:
    """A row with no id cannot be picked, exported or resumed — it is not a session,
    it is a shape."""
    with pytest.raises(ValueError, match="session_id is required"):
        SessionMeta(session_id="")


def test_unreadable_is_a_judgement_by_this_build_and_is_never_written_down(
    tmp_path: Path,
) -> None:
    """It must not round-trip. "This build cannot read it" is a fact about the reader,
    not about the session: recording it would make a file that one Ronin could not read
    claim to be unreadable by the Ronin that wrote it."""
    write_session(tmp_path)
    sidecar = json.loads(meta_path(tmp_path, SESSION_ID).read_text(encoding="utf-8"))
    assert "unreadable" not in sidecar

    header = json.loads(session_path(tmp_path, SESSION_ID).read_text().splitlines()[0])
    assert "unreadable" not in header
