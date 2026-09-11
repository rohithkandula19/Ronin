"""Which Ronin session a conversation out in the world is holding.

The load-bearing test is ``test_two_mentions_in_one_thread_share_one_session``:
get-or-create has to be atomic for the same reason the ledger claims before
acting, and a Retainer answering one thread twice from two halves of the context
is the failure it prevents.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from ronin.retainer.model import Channel
from ronin.retainer.threads import (
    THREADS_FILENAME,
    THREADS_SCHEMA_VERSION,
    Binding,
    ThreadMap,
    ThreadMapError,
)

WORKSPACE = Path("/srv/posts/ronin")
SESSION = "20260906-104500-abc123"
OTHER_SESSION = "20260906-110000-def456"


def threads(tmp_path: Path, **kwargs: object) -> ThreadMap:
    return ThreadMap.open(tmp_path / "retainer", **kwargs)  # type: ignore[arg-type]


def counting_mint() -> object:
    """A mint that would hand out a *different* id on every call."""
    counter = iter(f"20260906-1045{n:02d}-aaaaaa" for n in range(99))
    return lambda: next(counter)


# --------------------------------------------------------------------------- #
# Get-or-create
# --------------------------------------------------------------------------- #


def test_a_new_thread_gets_a_session_and_says_it_is_new(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    bound = table.bind("sentry", Channel.GITHUB, "258")
    assert bound.session == SESSION
    assert bound.fresh


def test_the_next_mention_lands_in_the_same_session(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=counting_mint())
    first = table.bind("sentry", Channel.GITHUB, "258")
    second = table.bind("sentry", Channel.GITHUB, "258")
    assert second.session == first.session
    assert not second.fresh


def test_two_mentions_in_one_thread_share_one_session(tmp_path: Path) -> None:
    """Real threads. Two mentions a second apart must not start two sessions."""
    table = threads(tmp_path, mint=counting_mint())
    start = threading.Barrier(8)
    seen: list[Binding] = []
    lock = threading.Lock()

    def mention() -> None:
        start.wait()
        bound = table.bind("sentry", Channel.GITHUB, "258")
        with lock:
            seen.append(bound)

    workers = [threading.Thread(target=mention) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert len({b.session for b in seen}) == 1, "one thread must hold exactly one session"
    assert [b.fresh for b in seen].count(True) == 1


def test_the_same_thread_id_on_two_channels_is_two_conversations(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=counting_mint())
    github = table.bind("sentry", Channel.GITHUB, "1")
    slack = table.bind("sentry", Channel.SLACK, "1")
    assert github.session != slack.session


def test_two_retainers_in_one_thread_keep_their_own_sessions(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=counting_mint())
    sentry = table.bind("sentry", Channel.GITHUB, "258")
    scout = table.bind("scout", Channel.GITHUB, "258")
    assert sentry.session != scout.session


def test_a_caller_may_supply_a_session_it_already_opened(tmp_path: Path) -> None:
    table = threads(tmp_path)
    assert table.bind("sentry", Channel.GITHUB, "258", session=SESSION).session == SESSION


def test_a_session_id_that_would_escape_the_sessions_directory_is_refused(
    tmp_path: Path,
) -> None:
    table = threads(tmp_path)
    with pytest.raises(ThreadMapError, match="not a usable session id"):
        table.bind("sentry", Channel.GITHUB, "258", session="../../etc/passwd")


def test_the_workspace_is_remembered_with_the_binding(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    bound = table.bind("sentry", Channel.GITHUB, "258", workspace=WORKSPACE)
    assert bound.workspace == WORKSPACE


def test_a_binding_without_a_workspace_reports_none_rather_than_a_bare_path(
    tmp_path: Path,
) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    assert table.bind("sentry", Channel.GITHUB, "258").workspace is None


def test_seen_at_advances_on_every_mention_and_bound_at_does_not(tmp_path: Path) -> None:
    ticks = iter([10.0, 20.0, 30.0])
    table = threads(tmp_path, clock=lambda: next(ticks), mint=lambda: SESSION)
    first = table.bind("sentry", Channel.GITHUB, "258")
    second = table.bind("sentry", Channel.GITHUB, "258")
    assert (first.bound_at, first.seen_at) == (10.0, 10.0)
    assert (second.bound_at, second.seen_at) == (10.0, 20.0)


# --------------------------------------------------------------------------- #
# Rebinding and forgetting
# --------------------------------------------------------------------------- #


def test_rebinding_moves_a_thread_to_a_new_session(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    table.bind("sentry", Channel.GITHUB, "258", workspace=WORKSPACE)
    moved = table.rebind("sentry", Channel.GITHUB, "258", OTHER_SESSION)
    assert moved.session == OTHER_SESSION
    assert moved.workspace == WORKSPACE


def test_rebinding_can_move_the_workspace_too(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    table.bind("sentry", Channel.GITHUB, "258", workspace=WORKSPACE)
    elsewhere = Path("/srv/posts/other")
    moved = table.rebind("sentry", Channel.GITHUB, "258", OTHER_SESSION, workspace=elsewhere)
    assert moved.workspace == elsewhere


def test_rebinding_something_never_bound_is_refused_rather_than_invented(
    tmp_path: Path,
) -> None:
    table = threads(tmp_path)
    with pytest.raises(ThreadMapError, match="is not bound"):
        table.rebind("sentry", Channel.GITHUB, "258", SESSION)


def test_rebinding_validates_the_session_id_too(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    table.bind("sentry", Channel.GITHUB, "258")
    with pytest.raises(ThreadMapError, match="not a usable session id"):
        table.rebind("sentry", Channel.GITHUB, "258", "../escape")


def test_forgetting_drops_the_pointer_and_reports_whether_there_was_one(
    tmp_path: Path,
) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    table.bind("sentry", Channel.GITHUB, "258")
    assert table.forget("sentry", Channel.GITHUB, "258")
    assert table.lookup("sentry", Channel.GITHUB, "258") is None
    assert not table.forget("sentry", Channel.GITHUB, "258")


def test_a_forgotten_thread_gets_a_new_session_rather_than_the_old_one(
    tmp_path: Path,
) -> None:
    table = threads(tmp_path, mint=counting_mint())
    first = table.bind("sentry", Channel.GITHUB, "258")
    table.forget("sentry", Channel.GITHUB, "258")
    assert table.bind("sentry", Channel.GITHUB, "258").session != first.session


# --------------------------------------------------------------------------- #
# Reading, including the direction a reply needs
# --------------------------------------------------------------------------- #


def test_lookup_creates_nothing(tmp_path: Path) -> None:
    table = threads(tmp_path)
    assert table.lookup("sentry", Channel.GITHUB, "258") is None
    assert table.recent() == ()


def test_a_session_can_be_traced_back_to_the_conversation_it_answers(
    tmp_path: Path,
) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    table.bind("sentry", Channel.SLACK, "C1/17.5")
    found = table.thread_for(SESSION)
    assert found is not None
    assert (found.channel, found.thread) == (Channel.SLACK, "C1/17.5")


def test_an_unknown_session_traces_to_nothing(tmp_path: Path) -> None:
    assert threads(tmp_path).thread_for(SESSION) is None


def test_recent_is_newest_first(tmp_path: Path) -> None:
    ticks = iter([10.0, 20.0, 30.0])
    table = threads(tmp_path, clock=lambda: next(ticks), mint=counting_mint())
    table.bind("sentry", Channel.GITHUB, "1")
    table.bind("sentry", Channel.GITHUB, "2")
    table.bind("sentry", Channel.GITHUB, "1")
    assert [b.thread for b in table.recent()] == ["1", "2"]


def test_recent_can_be_narrowed_and_limited(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=counting_mint())
    table.bind("sentry", Channel.GITHUB, "1")
    table.bind("scout", Channel.GITHUB, "2")
    assert [b.retainer for b in table.recent("scout")] == ["scout"]
    assert len(table.recent(limit=1)) == 1


# --------------------------------------------------------------------------- #
# Durability
# --------------------------------------------------------------------------- #


def test_bindings_survive_reopening(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    table.bind("sentry", Channel.GITHUB, "258", workspace=WORKSPACE)
    again = ThreadMap.open(tmp_path / "retainer")
    found = again.lookup("sentry", Channel.GITHUB, "258")
    assert found is not None
    assert (found.session, found.workspace) == (SESSION, WORKSPACE)
    assert not found.fresh


def test_a_schema_it_does_not_understand_is_refused(tmp_path: Path) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    table.bind("sentry", Channel.GITHUB, "258")
    with sqlite3.connect(table.path) as conn:
        conn.execute(f"PRAGMA user_version={THREADS_SCHEMA_VERSION + 1}")
    with pytest.raises(ThreadMapError, match="forget every conversation"):
        ThreadMap.open(tmp_path / "retainer")
    with sqlite3.connect(table.path) as conn:
        assert conn.execute("SELECT count(*) FROM threads").fetchone()[0] == 1


def test_a_file_that_is_not_a_database_raises(tmp_path: Path) -> None:
    directory = tmp_path / "retainer"
    directory.mkdir()
    (directory / THREADS_FILENAME).write_bytes(b"not a database")
    with pytest.raises(ThreadMapError, match="cannot prepare"):
        ThreadMap.open(directory)


def test_a_path_that_cannot_be_opened_at_all_raises(tmp_path: Path) -> None:
    directory = tmp_path / "retainer"
    directory.mkdir()
    (directory / THREADS_FILENAME).mkdir()
    with pytest.raises(ThreadMapError, match="cannot open the thread map"):
        ThreadMap.open(directory)


def test_every_operation_raises_when_the_file_is_destroyed_under_us(
    tmp_path: Path,
) -> None:
    table = threads(tmp_path, mint=lambda: SESSION)
    table.bind("sentry", Channel.GITHUB, "258")
    table.path.write_bytes(b"clobbered")
    with pytest.raises(ThreadMapError, match="cannot bind"):
        table.bind("sentry", Channel.GITHUB, "259")
    with pytest.raises(ThreadMapError, match="cannot rebind"):
        table.rebind("sentry", Channel.GITHUB, "258", OTHER_SESSION)
    with pytest.raises(ThreadMapError, match="cannot forget"):
        table.forget("sentry", Channel.GITHUB, "258")
    with pytest.raises(ThreadMapError, match="cannot read"):
        table.lookup("sentry", Channel.GITHUB, "258")
    with pytest.raises(ThreadMapError, match="cannot read"):
        table.thread_for(SESSION)
    with pytest.raises(ThreadMapError, match="cannot read"):
        table.recent()


def test_the_default_mint_produces_a_usable_session_id(tmp_path: Path) -> None:
    """The injected default must satisfy the validator the setter enforces."""
    from ronin.persistence.transcript import valid_session_id

    bound = threads(tmp_path).bind("sentry", Channel.GITHUB, "258")
    assert valid_session_id(bound.session)
