"""The sqlite session index: derived, droppable, and never fatal.

The index earns its place by answering two questions the filesystem cannot: *"which
session cost the most"* and *"which one was the pagination bug"*. Everything else in
this file is about the promise that makes it safe to have at all — **it is a cache** —
so the tests are grouped by the three claims that promise decomposes into:

1. it holds what the transcripts hold (round trip, exactly, including the tuples),
2. it can always be rebuilt from them, and rebuilding fixes anything (deleted file,
   wrong schema version, half-populated rows),
3. it cannot take a session down with it — a write that fails is recorded and the turn
   carries on, while a *read* that fails raises rather than returning "no matches",
   because those two lies have very different costs.

Offline by construction: sqlite is a local file and this layer has no provider seam.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from persistence_harness import (
    full_state,
    two_turn_session,
)

from ronin.core.types import (
    AgentState,
    Budget,
    Event,
    Message,
    Role,
    Text,
    TextDelta,
    ToolEnd,
    ToolResult,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.persistence import index as index_module
from ronin.persistence.index import (
    INDEX_FILENAME,
    INDEX_SCHEMA_VERSION,
    RebuildReport,
    SearchHit,
    SessionIndex,
    SessionIndexError,
    fts_query,
    index_path,
    searchable_turns,
)
from ronin.persistence.transcript import (
    SessionMeta,
    Transcript,
    list_sessions,
    meta_path,
    session_path,
)


def turn_events(index: int, prompt: str, answer: str, *, cost: float = 0.0) -> list[Event]:
    """One complete turn: a prompt in the recorded state, prose, and an end.

    The prompt lives in ``TurnEnd.agent_state`` and not in an event, because that is
    where the real loop puts it — there is no ``UserMessage`` event in this system, and
    a helper that invented one would test a stream Ronin never writes.
    """
    state = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text(text=prompt),)),),
        budget=Budget(spent_usd=cost),
    )
    return [
        TurnStart(turn_index=index),
        TextDelta(text=answer),
        TurnEnd(turn_index=index, state=TurnState.DONE, agent_state=state),
    ]


def recorded(directory: Path, session_id: str, turns: list[list[Event]], **kwargs: object) -> None:
    """Write a session through the real ``Transcript``, with the real index attached."""
    index = kwargs.pop("index", None)
    with Transcript.open(directory, session_id, index=index, **kwargs) as transcript:  # type: ignore[arg-type]
        for turn in turns:
            for event in turn:
                transcript.append(event)


# --------------------------------------------------------------------------- #
# 1. it holds what the transcripts hold
# --------------------------------------------------------------------------- #


def test_a_session_round_trips_through_the_index_unchanged(tmp_path: Path) -> None:
    """Every ``SessionMeta`` field comes back, including the two tuples.

    ``files_touched`` and ``checkpoint_ids`` are JSON in ``TEXT`` columns, which is the
    one place this schema is lossy if it is wrong — a tuple that comes back as a string
    of its own repr would still *look* right in a picker row.
    """
    index = SessionIndex.open(tmp_path)
    meta = SessionMeta(
        session_id="20260811-120000-aaaaaa",
        cwd="/work/repo",
        model="claude-opus-5",
        title="the pagination bug",
        started_at=1_000.5,
        updated_at=2_000.25,
        turns=7,
        events=143,
        cost_usd=1.2345,
        duration_seconds=987.5,
        files_touched=("src/a.py", "src/b.py"),
        checkpoint_ids=("ck-1", "ck-2", "ck-3"),
    )
    assert index.record(meta) is True
    (back,) = index.recent()
    assert back == meta


def test_the_index_returns_the_type_the_picker_already_understands(tmp_path: Path) -> None:
    """``recent`` yields ``SessionMeta``, so no caller learns that sqlite exists.

    This is what keeps the index optional: the plain listing and the indexed listing
    are interchangeable values, so a broken database changes which code path runs and
    nothing about what the caller does with the result.
    """
    index = SessionIndex.open(tmp_path)
    index.record(SessionMeta(session_id="s1", cwd="/w", turns=2))
    (row,) = index.recent()
    assert isinstance(row, SessionMeta)
    assert row.loadable


def test_sessions_come_back_newest_first_and_can_be_ordered_by_cost(tmp_path: Path) -> None:
    """``--by-cost`` is the reason to have an index at all: the filesystem cannot sort
    by a number stored inside a file's sidecar without opening every one of them."""
    index = SessionIndex.open(tmp_path)
    index.record(SessionMeta(session_id="old-cheap", updated_at=100.0, cost_usd=0.10))
    index.record(SessionMeta(session_id="new-pricey", updated_at=300.0, cost_usd=9.99))
    index.record(SessionMeta(session_id="mid-medium", updated_at=200.0, cost_usd=1.50))

    assert [m.session_id for m in index.recent()] == ["new-pricey", "mid-medium", "old-cheap"]
    assert [m.session_id for m in index.costliest()] == ["new-pricey", "mid-medium", "old-cheap"]
    assert [m.session_id for m in index.costliest(limit=1)] == ["new-pricey"]


def test_recent_can_be_narrowed_to_one_directory(tmp_path: Path) -> None:
    """``--continue`` means "the latest session *here*", so cwd has to be queryable."""
    index = SessionIndex.open(tmp_path)
    index.record(SessionMeta(session_id="here-1", cwd="/repo/one", updated_at=2.0))
    index.record(SessionMeta(session_id="there", cwd="/repo/two", updated_at=3.0))
    index.record(SessionMeta(session_id="here-2", cwd="/repo/one", updated_at=1.0))
    assert [m.session_id for m in index.recent(cwd="/repo/one")] == ["here-1", "here-2"]


def test_a_turns_prompt_and_answer_are_both_searchable(tmp_path: Path) -> None:
    """Both halves, because people search for what they asked *and* for what they were
    told, and only one of the two is in the event stream."""
    index = SessionIndex.open(tmp_path)
    recorded(
        tmp_path,
        "20260811-120000-aaaaaa",
        [turn_events(0, "fix the flaky pagination test", "I patched tests/test_pager.py")],
        index=index,
    )
    assert [h.role for h in index.search("pagination")] == ["user"]
    assert [h.role for h in index.search("test_pager")] == ["assistant"]


def test_a_search_hit_carries_a_snippet_that_shows_the_match(tmp_path: Path) -> None:
    """A list of session ids is not an answer — the whole point is recognising the
    session, which means seeing the words in context."""
    index = SessionIndex.open(tmp_path)
    recorded(
        tmp_path,
        "20260811-120000-aaaaaa",
        [turn_events(0, "the webhook dispatcher drops retries under load", "Added backoff")],
        index=index,
    )
    (hit,) = index.search("webhook")
    assert isinstance(hit, SearchHit)
    assert "webhook" in hit.snippet
    assert "<<webhook>>" in hit.snippet, "the matched term should be marked"
    assert hit.session_id == "20260811-120000-aaaaaa"
    assert hit.turn == 0


def test_search_requires_every_word_rather_than_any(tmp_path: Path) -> None:
    """AND, not OR. A search box that widens as you type more words is one that gets
    less useful the harder you try to be specific."""
    index = SessionIndex.open(tmp_path)
    recorded(tmp_path, "s1", [turn_events(0, "refactor the auth middleware", "done")], index=index)
    recorded(tmp_path, "s2", [turn_events(0, "refactor the pager", "done")], index=index)
    assert {h.session_id for h in index.search("refactor")} == {"s1", "s2"}
    assert {h.session_id for h in index.search("refactor auth")} == {"s1"}
    assert index.search("refactor auth pagination") == ()


def test_text_a_stream_reset_discarded_is_not_searchable(tmp_path: Path) -> None:
    """Reset text is text the model retracted. Indexing it would surface a session by
    a sentence it does not contain — and ``two_turn_session`` carries exactly that
    case, so the fold is what this relies on rather than a rule of its own."""
    rows = searchable_turns(two_turn_session())
    bodies = " ".join(body for _turn, _role, body in rows)
    assert "DISCARD ME" not in bodies
    assert "handler() returns None." in bodies


def test_only_the_turns_own_prompt_is_indexed_for_it(tmp_path: Path) -> None:
    """``agent_state.messages`` is the whole conversation, so the *last* user message
    is this turn's question. Taking the first would file every turn in a long session
    under turn one's prompt, and search would answer with the wrong turn every time."""
    first = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text(text="question one"),)),)
    )
    second = AgentState(
        messages=(
            Message(role=Role.USER, content_blocks=(Text(text="question one"),)),
            Message(role=Role.ASSISTANT, content_blocks=(Text(text="answer one"),)),
            Message(role=Role.USER, content_blocks=(Text(text="question two"),)),
        )
    )
    events: list[Event] = [
        TurnStart(turn_index=0),
        TurnEnd(turn_index=0, state=TurnState.DONE, agent_state=first),
        TurnStart(turn_index=1),
        TurnEnd(turn_index=1, state=TurnState.DONE, agent_state=second),
    ]
    prompts = {turn: body for turn, role, body in searchable_turns(events) if role == "user"}
    assert prompts == {0: "question one", 1: "question two"}


def test_a_turn_with_no_recorded_state_contributes_no_prompt() -> None:
    """A replayed recording has no ``agent_state`` to hand back. That is a documented
    shape, not a broken one, so it yields no user row instead of raising."""
    events: list[Event] = [
        TurnStart(turn_index=0),
        TextDelta(text="prose survives"),
        TurnEnd(turn_index=0, state=TurnState.DONE),
    ]
    rows = searchable_turns(events)
    assert [role for _turn, role, _body in rows] == ["assistant"]


def test_tool_output_is_not_indexed(tmp_path: Path) -> None:
    """Deliberate: tool results are most of a transcript's bytes and are where fetched
    pages land. An index that searched them would rival the logs in size and answer
    with a line of somebody else's HTML."""
    index = SessionIndex.open(tmp_path)
    events: list[Event] = [
        TurnStart(turn_index=0),
        ToolEnd(
            tool_use_id="t1",
            name="read",
            result=ToolResult(ok=True, content="ZEBRAFISH_SENTINEL in the file body"),
        ),
        *turn_events(0, "look at the file", "I read it"),
    ]
    recorded(tmp_path, "s1", [events], index=index)
    assert index.search("ZEBRAFISH_SENTINEL") == ()
    assert index.search("look at the file") != ()


# --------------------------------------------------------------------------- #
# the query builder
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("pagination bug", '"pagination" "bug"'),
        ("don't", '"don\'t"'),
        # A smart apostrophe, spelled as an escape so nobody "tidies" it into an ASCII
        # one and quietly deletes the case this row exists to cover.
        ("don\u2019t", '"don\u2019t"'),
        ("NOT here", '"NOT" "here"'),
        ("a OR b", '"a" "OR" "b"'),
        ("wild*card", '"wild" "card"'),
        ("snake_case", '"snake" "case"'),
        ("", ""),
        ("   ", ""),
        ("!!! ???", ""),
        ('a "quoted" phrase', '"a" "quoted" "phrase"'),
    ],
)
def test_a_query_is_rebuilt_from_words_never_forwarded(text: str, expected: str) -> None:
    """Every word quoted, every operator neutralised, every apostrophe survived.

    fts5 ``MATCH`` is an expression language, and forwarding a typed phrase into it
    means ``don't`` raises ``unterminated string`` while a sentence containing "not"
    silently inverts the search. Both are verified against real sqlite below."""
    assert fts_query(text) == expected


def test_every_punctuation_mark_is_safe_to_type_into_a_search(tmp_path: Path) -> None:
    """The claim above, executed. Each of these crashes or misbehaves if the raw string
    reaches ``MATCH``; none of them may raise here."""
    index = SessionIndex.open(tmp_path)
    recorded(tmp_path, "s1", [turn_events(0, "the pager doesn't paginate", "fixed")], index=index)
    for query in (
        "doesn't",
        'a "b',
        "NOT pager",
        "pager OR",
        "AND",
        "*",
        "^pager",
        "pager NEAR/2",
        "((",
        "-",
        "'",
        '"',
    ):
        index.search(query)  # must not raise
    assert [h.session_id for h in index.search("doesn't paginate")] == ["s1"]


def test_a_wordless_query_finds_nothing_and_says_so_by_returning_empty(tmp_path: Path) -> None:
    """Not an error: there is nothing to look for. The caller decides how to phrase
    that, which is why it is ``()`` and not an exception."""
    index = SessionIndex.open(tmp_path)
    assert index.search("") == ()
    assert index.search("  ?!  ") == ()


# --------------------------------------------------------------------------- #
# 2. it can always be rebuilt
# --------------------------------------------------------------------------- #


def test_a_deleted_index_is_rebuilt_from_the_transcripts(tmp_path: Path) -> None:
    """The claim that makes the whole design safe: ``rm index.sqlite3`` loses nothing.

    This is the recovery procedure every error message in the module points at, so it
    has to work from a standing start with no index file at all.
    """
    index = SessionIndex.open(tmp_path)
    recorded(
        tmp_path,
        "20260811-120000-aaaaaa",
        [turn_events(0, "fix the pagination bug", "patched", cost=0.5)],
        index=index,
    )
    index_path(tmp_path).unlink()

    fresh = SessionIndex.open(tmp_path)
    assert fresh.count() == 0
    report = fresh.rebuild(tmp_path)
    assert isinstance(report, RebuildReport)
    assert report.indexed == 1
    assert report.skipped == ()
    assert fresh.count() == 1
    assert [h.session_id for h in fresh.search("pagination")] == ["20260811-120000-aaaaaa"]


def test_a_rebuild_keeps_the_costs_instead_of_zeroing_them(tmp_path: Path) -> None:
    """The bug this design avoids by construction.

    A transcript's *header* is identity-only — its counters are from before the first
    turn — so rebuilding from header lines would quietly replace every real cost with
    ``0.0``. The rebuild reads ``list_sessions``, which prefers the sidecar, so the
    numbers survive. A cache that loses your money on repair is not a repair.
    """
    index = SessionIndex.open(tmp_path)
    recorded(
        tmp_path,
        "20260811-120000-aaaaaa",
        [
            turn_events(0, "first", "one", cost=0.25),
            turn_events(1, "second", "two", cost=1.75),
        ],
        index=index,
    )
    live = index.recent()[0]
    assert live.cost_usd == pytest.approx(1.75)
    assert live.turns == 2

    index_path(tmp_path).unlink()
    fresh = SessionIndex.open(tmp_path)
    fresh.rebuild(tmp_path)
    rebuilt = fresh.recent()[0]
    assert rebuilt.cost_usd == pytest.approx(1.75), "a rebuild must not zero the cost"
    assert rebuilt.turns == 2
    assert rebuilt.session_id == live.session_id


def test_a_rebuild_drops_sessions_whose_transcript_is_gone(tmp_path: Path) -> None:
    """A deleted session that still shows in the picker is worse than a slow picker."""
    index = SessionIndex.open(tmp_path)
    recorded(tmp_path, "keep-me", [turn_events(0, "keep", "ok")], index=index)
    recorded(tmp_path, "delete-me", [turn_events(0, "delete", "ok")], index=index)
    assert index.count() == 2

    session_path(tmp_path, "delete-me").unlink()
    meta_path(tmp_path, "delete-me").unlink()
    report = index.rebuild(tmp_path)
    assert report.removed == 1
    assert [m.session_id for m in index.recent()] == ["keep-me"]
    assert index.search("delete") == ()


def test_a_rebuild_is_idempotent(tmp_path: Path) -> None:
    """Run it twice, get the same index. Otherwise the recovery procedure is a way to
    double every row, and a search would start returning each hit twice."""
    index = SessionIndex.open(tmp_path)
    recorded(tmp_path, "s1", [turn_events(0, "the pagination bug", "fixed")], index=index)
    first = index.rebuild(tmp_path)
    hits_once = index.search("pagination")
    second = index.rebuild(tmp_path)
    assert (first.indexed, first.removed) == (second.indexed, second.removed)
    assert index.search("pagination") == hits_once
    assert index.count() == 1


def test_an_index_from_another_schema_version_is_dropped_not_migrated(tmp_path: Path) -> None:
    """A cache with a migration story is a cache pretending to be a database.

    The old rows go, the file stays valid, and a rebuild refills it. What must *not*
    happen is a half-old schema that answers queries with a mix of two shapes.
    """
    index = SessionIndex.open(tmp_path)
    index.record(SessionMeta(session_id="from-the-old-build", turns=3))
    assert index.count() == 1

    with sqlite3.connect(index.path) as conn:
        conn.execute(f"PRAGMA user_version={INDEX_SCHEMA_VERSION + 41}")

    reopened = SessionIndex.open(tmp_path)
    assert reopened.count() == 0, "rows from another schema version must be dropped"
    with sqlite3.connect(reopened.path) as conn:
        stamped = int(conn.execute("PRAGMA user_version").fetchone()[0])
    assert stamped == INDEX_SCHEMA_VERSION
    reopened.record(SessionMeta(session_id="from-this-build"))
    assert [m.session_id for m in reopened.recent()] == ["from-this-build"]


def test_a_rebuild_lists_an_unreadable_session_and_says_it_is_not_searchable(
    tmp_path: Path,
) -> None:
    """A session written by another schema version still exists, and vanishing from
    the picker is the worst thing that could happen to it — that is the moment the user
    most needs to be told it is there. So: metadata row yes, text no, reason recorded.
    """
    recorded(tmp_path, "20260811-120000-aaaaaa", [turn_events(0, "hello", "hi")])
    path = session_path(tmp_path, "20260811-120000-aaaaaa")
    meta_path(tmp_path, "20260811-120000-aaaaaa").unlink()
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        lines[0].replace('"v":1', '"v":99').replace('"v": 1', '"v": 99') + "\n",
        encoding="utf-8",
    )

    index = SessionIndex.open(tmp_path)
    report = index.rebuild(tmp_path)
    assert report.indexed == 1, "the row must still be listed"
    assert any("not searchable" in note for note in report.skipped)
    (row,) = index.recent()
    assert not row.loadable
    assert index.search("hello") == ()


def test_a_rebuild_ignores_files_that_are_not_sessions(tmp_path: Path) -> None:
    """One odd filename in the directory must not cost the whole rebuild.

    Three shapes, each a real thing that ends up in a directory people can see: a name
    no session id may have, a stray non-transcript file, and an empty ``.jsonl`` from a
    crash before the header was written.
    """
    recorded(tmp_path, "real-session", [turn_events(0, "real", "yes")])
    (tmp_path / "not an id!.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a transcript", encoding="utf-8")
    (tmp_path / "20260811-999999-empty.jsonl").write_text("", encoding="utf-8")

    index = SessionIndex.open(tmp_path)
    report = index.rebuild(tmp_path)
    assert report.indexed == 1
    assert [m.session_id for m in index.recent()] == ["real-session"]
    assert any("not a valid session id" in note for note in report.skipped)
    assert any("nothing there to describe" in note for note in report.skipped)


def test_a_rebuild_lists_a_corrupt_transcript_without_letting_it_stop_the_rest(
    tmp_path: Path,
) -> None:
    """A hole in the middle of a log makes it unreadable — but it still has a sidecar,
    so it is still listed, with its own reason, and the healthy session beside it still
    gets indexed. One bad file costing the whole rebuild is the failure mode that makes
    people stop running the repair command."""
    recorded(tmp_path, "healthy", [turn_events(0, "the pagination bug", "fixed")])
    recorded(tmp_path, "damaged", [turn_events(0, "something", "else")])
    path = session_path(tmp_path, "damaged")
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "{ this is not json }")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    index = SessionIndex.open(tmp_path)
    report = index.rebuild(tmp_path)
    assert report.indexed == 2, "both sessions are listed"
    assert any("damaged" in note and "not searchable" in note for note in report.skipped)
    assert [h.session_id for h in index.search("pagination")] == ["healthy"]


def test_a_state_with_no_user_message_yields_no_prompt() -> None:
    """An assistant-only state is not a bug — a nudged or tool-only turn can produce
    one — so it contributes no user row rather than raising or indexing the wrong role.
    """
    state = AgentState(
        messages=(Message(role=Role.ASSISTANT, content_blocks=(Text(text="unprompted"),)),)
    )
    events: list[Event] = [
        TurnStart(turn_index=0),
        TurnEnd(turn_index=0, state=TurnState.DONE, agent_state=state),
    ]
    assert searchable_turns(events) == ()


def test_the_summary_names_what_a_rebuild_did(tmp_path: Path) -> None:
    """The text a user reads after ``--reindex``. Counts, and what was skipped."""
    assert "0 session(s) indexed" in RebuildReport().summary()
    loud = RebuildReport(indexed=3, removed=2, skipped=("a: why", "b: why"))
    assert "3 session(s) indexed" in loud.summary()
    assert "2 stale row(s) dropped" in loud.summary()
    assert "2 skipped" in loud.summary()


# --------------------------------------------------------------------------- #
# 3. it cannot take a session down
# --------------------------------------------------------------------------- #


def test_a_write_to_an_impossible_index_is_recorded_not_raised(tmp_path: Path) -> None:
    """The rule that makes the index safe to enable by default.

    ``record`` runs at a turn boundary, *after* the transcript is fsynced. If it could
    raise, a full disk or a locked database would end a session whose work is already
    safely on disk — trading a degraded cache for lost work.
    """
    wall = tmp_path / "not-a-directory"
    wall.write_text("I am a file where a directory should be", encoding="utf-8")
    index = SessionIndex.open(wall)
    assert index.record(SessionMeta(session_id="s1")) is False
    assert index.degraded
    assert any("index" in problem for problem in index.problems)


def test_a_failing_index_does_not_stop_the_transcript_recording_a_turn(tmp_path: Path) -> None:
    """The integration form of the same rule, through the real ``Transcript``.

    The transcript is the truth; the index is a cache of it. So a session whose index
    is unusable still records every event, still fsyncs, and still resumes — the only
    thing lost is the ability to search it, which a rebuild restores.
    """
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    wall = tmp_path / "blocked"
    wall.write_text("not a directory", encoding="utf-8")
    broken = SessionIndex.open(wall)

    recorded(sessions, "s1", [turn_events(0, "still recorded?", "yes")], index=broken)

    (meta,) = list_sessions(sessions)
    assert meta.turns == 1, "the transcript must be complete despite the index failing"
    assert broken.degraded


def test_the_same_write_problem_is_recorded_once_however_often_it_happens(
    tmp_path: Path,
) -> None:
    """Four hundred identical warnings tell the user nothing — the lesson the
    transcript recorder in ``cli/stream.py`` already learned.

    Deduplication is per *message*, not a global cap, so genuinely different failures
    still each get said once. This index has two: it could not be prepared, and then it
    could not be written. Five failing writes add one line between them, not five.
    """
    wall = tmp_path / "wall"
    wall.write_text("x", encoding="utf-8")
    index = SessionIndex.open(wall)
    after_open = list(index.problems)
    for number in range(5):
        index.record(SessionMeta(session_id=f"s{number}"))
    assert len(index.problems) == len(after_open) + 1
    assert len(set(index.problems)) == len(index.problems), "no message repeats"


def test_a_read_from_a_corrupt_index_raises_instead_of_saying_no_matches(
    tmp_path: Path,
) -> None:
    """The asymmetry, and the reason for it: ``()`` from a broken index reads exactly
    like "you never had that session", which sends someone looking for their work in
    the wrong place. A raise makes the caller say what is actually wrong."""
    index = SessionIndex.open(tmp_path)
    index.record(SessionMeta(session_id="s1"))
    index.path.write_bytes(b"this is not a sqlite database, not even close")

    with pytest.raises(SessionIndexError, match="reindex"):
        index.recent()
    with pytest.raises(SessionIndexError):
        index.count()


def test_opening_a_corrupt_index_replaces_it_instead_of_inheriting_the_damage(
    tmp_path: Path,
) -> None:
    """The other half of the previous test, and the more useful half.

    A handle that was open when the file went bad reports the failure. A *new* handle
    finds a file sqlite will not open and replaces it, because the file is a cache and
    the alternative is a Ronin that cannot start a session until someone deletes a
    database by hand. The repair is announced rather than silent — a user whose search
    history just vanished should be told why.
    """
    index = SessionIndex.open(tmp_path)
    recorded(tmp_path, "s1", [turn_events(0, "the pagination bug", "fixed")], index=index)
    index.path.write_bytes(b"truncated garbage where a database used to be")

    fresh = SessionIndex.open(tmp_path)
    assert fresh.search("anything") == (), "a replaced index is empty, not broken"
    assert fresh.count() == 0
    assert any("not a readable database" in problem for problem in fresh.problems)
    assert any("nothing was lost" in problem for problem in fresh.problems)

    # And the repair is complete: a rebuild refills it from the transcripts.
    assert fresh.rebuild(tmp_path).indexed == 1
    assert [h.session_id for h in fresh.search("pagination")] == ["s1"]


def test_a_locked_index_is_never_deleted(tmp_path: Path) -> None:
    """The line between "corrupt" and "busy".

    A lock means another Ronin is mid-write, and deleting a database that process has
    open would turn a moment's contention into two corrupt indexes. Only a file sqlite
    reports as *not a database* is replaced, so a ``OperationalError`` must propagate
    out of ``_prepare`` with the file still on disk.
    """
    index = SessionIndex.open(tmp_path)
    index.record(SessionMeta(session_id="s1"))
    before = index.path.read_bytes()

    with sqlite3.connect(index.path, isolation_level=None) as holder:
        holder.execute("BEGIN EXCLUSIVE")
        contender = SessionIndex(path=index.path, timeout=0.01)
        # Never raises out of `record`, and — the point — never removes the file.
        assert contender.record(SessionMeta(session_id="s2")) is False
        assert index.path.exists()
        holder.execute("ROLLBACK")

    assert index.path.read_bytes() == before or index.count() == 1


def test_the_error_names_the_repair(tmp_path: Path) -> None:
    """An error that does not say what to do is a dead end. The repair is always the
    same one, so it is always named."""
    index = SessionIndex.open(tmp_path)
    index.path.write_bytes(b"garbage")
    with pytest.raises(SessionIndexError) as caught:
        index.recent()
    message = str(caught.value)
    assert "cache" in message
    assert "reindex" in message
    assert "nothing is lost" in message


def test_a_column_written_by_something_else_does_not_crash_a_listing(tmp_path: Path) -> None:
    """``files_touched`` is JSON in a ``TEXT`` column, so it is the one field another
    program (or a hand-edited row) can make nonsense of. A picker that raised on it
    would be unable to list the session that needs looking at most."""
    index = SessionIndex.open(tmp_path)
    index.record(SessionMeta(session_id="s1", files_touched=("a.py",)))
    with sqlite3.connect(index.path) as conn:
        conn.execute(
            "UPDATE sessions SET files_touched = 'not json', checkpoint_ids = '{\"o\": 1}'"
        )
    (row,) = index.recent()
    assert row.files_touched == ()
    assert row.checkpoint_ids == (), "a JSON object where a list belongs is not a list"


def test_writes_that_fail_after_the_index_opened_are_still_only_reported(
    tmp_path: Path,
) -> None:
    """The database was fine at open and broke later — a disk filling up mid-session.
    Both write paths have to stay non-fatal, not just the one that runs first."""
    index = SessionIndex.open(tmp_path)
    index.record(SessionMeta(session_id="s1"))
    index.path.write_bytes(b"no longer a database")

    assert index.record(SessionMeta(session_id="s2")) is False
    assert index.forget("s1") is False
    assert index.rebuild(tmp_path).indexed == 0
    assert index.degraded


def test_without_fts5_metadata_still_indexes_and_only_search_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sqlite built without the fts5 module is a real configuration, and it must cost
    the *search* feature rather than the index.

    Simulated by making the virtual-table statement fail, which is what that build does
    at exactly this line. The listing keeps working, the refusal explains itself, and
    the reason is recorded once — the alternative, an index that will not open at all,
    would take ``--by-cost`` and the picker down with a feature nobody asked for.
    """
    monkeypatch.setattr(index_module, "FTS_SCHEMA", "CREATE VIRTUAL TABLE t USING no_such_module;")
    index = SessionIndex.open(tmp_path)

    assert index.searchable is False
    assert any("fts5" in problem for problem in index.problems)
    assert index.record(SessionMeta(session_id="s1", cost_usd=2.0)) is True
    assert [m.session_id for m in index.recent()] == ["s1"]
    assert [m.session_id for m in index.costliest()] == ["s1"]
    with pytest.raises(SessionIndexError, match="fts5"):
        index.search("anything")


def test_a_rebuild_that_cannot_open_a_database_reports_instead_of_raising(
    tmp_path: Path,
) -> None:
    """``--reindex`` is the repair command, so it is the last place that may traceback.

    Pointed at a path that cannot hold a database at all — a file where the directory
    should be — it returns a report saying nothing was indexed and why.
    """
    wall = tmp_path / "not-a-directory"
    wall.write_text("x", encoding="utf-8")
    index = SessionIndex.open(wall)

    report = index.rebuild(wall)

    assert report.indexed == 0
    assert report.skipped != ()
    assert index.degraded


def test_forgetting_a_session_removes_its_rows_and_its_text(tmp_path: Path) -> None:
    """``forget`` is how a deleted session leaves the index without a full rebuild."""
    index = SessionIndex.open(tmp_path)
    recorded(tmp_path, "s1", [turn_events(0, "the pagination bug", "fixed")], index=index)
    assert index.search("pagination") != ()
    assert index.forget("s1") is True
    assert index.recent() == ()
    assert index.search("pagination") == ()


# --------------------------------------------------------------------------- #
# the transcript seam
# --------------------------------------------------------------------------- #


def test_the_transcript_indexes_at_the_turn_boundary_and_not_per_event(
    tmp_path: Path,
) -> None:
    """The same cadence as the fsync and the sidecar, for the same reason: a streaming
    turn emits hundreds of deltas, and a database write per delta would make the cache
    the slowest thing in the loop.

    Opening a fresh session writes its metadata row once — so it is in the picker
    before its first turn ends — and that write carries no events. What must not happen
    is a write *per delta*, which is what the mid-turn assertion pins.
    """
    calls: list[tuple[str, int]] = []

    class Spy:
        def record_turn(self, meta: SessionMeta, events: object = ()) -> bool:
            assert isinstance(events, tuple)
            calls.append((meta.session_id, len(events)))
            return True

    with Transcript.open(tmp_path, "s1", index=Spy()) as transcript:
        assert calls == [("s1", 0)], "the header sync registers the session, with no text"
        transcript.append(TurnStart(turn_index=0))
        for _ in range(20):
            transcript.append(TextDelta(text="chunk "))
        assert len(calls) == 1, "no write may happen per event"
        transcript.append(TurnEnd(turn_index=0, state=TurnState.DONE, agent_state=full_state()))
        assert len(calls) == 2
        assert calls[1][1] == 22, "the turn's events, all of them, exactly once"


def test_each_turn_hands_over_only_its_own_events(tmp_path: Path) -> None:
    """Bounded memory: the writer keeps the current turn, not the session. A long
    session must not accumulate every delta it ever streamed just so the cache can
    re-read text it already has — that is a leak with a docstring.

    Three turns of three events each, so a growing prefix would show as ``3, 6, 9``.
    The leading and trailing zeroes are the header sync and ``close``, which carry
    metadata and no text.
    """
    sizes: list[int] = []

    class Spy:
        def record_turn(self, meta: SessionMeta, events: object = ()) -> bool:
            assert isinstance(events, tuple)
            sizes.append(len(events))
            return True

    with Transcript.open(tmp_path, "s1", index=Spy()) as transcript:
        for index in range(3):
            for event in turn_events(index, f"prompt {index}", f"answer {index}"):
                transcript.append(event)
    assert sizes == [0, 3, 3, 3, 0], "each turn's own events only — never a growing prefix"


def test_a_session_that_ends_mid_turn_still_indexes_the_abandoned_prompt(
    tmp_path: Path,
) -> None:
    """``close`` flushes whatever the unfinished turn accumulated.

    The interrupted turn is the one a user is most likely to come looking for — they
    asked for something and did not get it — so its prompt has to be searchable even
    though no ``TurnEnd`` was ever recorded for it.
    """
    index = SessionIndex.open(tmp_path)
    state = AgentState(
        messages=(Message(role=Role.USER, content_blocks=(Text(text="deploy the hotfix"),)),)
    )
    with Transcript.open(tmp_path, "s1", index=index) as transcript:
        transcript.append(TurnStart(turn_index=0))
        transcript.append(TextDelta(text="starting the deploy"))
        transcript.append(TurnEnd(turn_index=0, state=TurnState.DONE, agent_state=state))
        # A second turn that never ends: the process is about to be killed.
        transcript.append(TurnStart(turn_index=1))
        transcript.append(TextDelta(text="rolling back because the smoke test failed"))
    assert [h.session_id for h in index.search("rolling back smoke")] == ["s1"]


def test_a_transcript_with_no_index_behaves_exactly_as_before(tmp_path: Path) -> None:
    """The index is opt-in at this seam. Every existing caller passes nothing, and this
    pins that the default costs nothing and touches no database."""
    with Transcript.open(tmp_path, "s1") as transcript:
        for event in turn_events(0, "hello", "hi"):
            transcript.append(event)
    assert not index_path(tmp_path).exists()
    (meta,) = list_sessions(tmp_path)
    assert meta.turns == 1


def test_a_resumed_turn_does_not_double_its_rows(tmp_path: Path) -> None:
    """Re-recording a turn replaces its text rather than adding a second copy, so a
    resume that re-writes the last turn cannot make search return it twice."""
    index = SessionIndex.open(tmp_path)
    meta = SessionMeta(session_id="s1", turns=1)
    rows = searchable_turns(turn_events(0, "the pagination bug", "fixed"))
    index.record(meta, rows)
    index.record(meta, rows)
    index.record(meta, rows)
    assert len(index.search("pagination")) == 1


def test_the_index_satisfies_the_protocol_the_transcript_declares(tmp_path: Path) -> None:
    """``Transcript`` depends on a structural ``Indexer``, not on this class — which is
    what keeps ``persistence.transcript`` free of an import cycle. If the shapes drift,
    the wiring in ``cli/wire.py`` is where it would show, so it is pinned here."""
    from ronin.persistence.transcript import Indexer

    index: Indexer = SessionIndex.open(tmp_path)
    assert index.record_turn(SessionMeta(session_id="s1"), ()) is True


def test_the_default_filename_is_stable(tmp_path: Path) -> None:
    """Users delete this file by name when it misbehaves, and the docs say which name."""
    assert INDEX_FILENAME == "index.sqlite3"
    assert index_path(tmp_path) == tmp_path / "index.sqlite3"
