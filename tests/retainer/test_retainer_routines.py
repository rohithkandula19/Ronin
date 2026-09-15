"""Routines: a Retainer waking itself on the same path a mention uses.

Three properties here are the reason this step was built last.

``test_a_machine_that_slept_for_a_week_fires_once`` is the runaway-spend failure,
and it arrives all at once the moment a laptop wakes.

``test_a_routine_cannot_name_another_retainer`` checks that bot-to-bot chatter is
*unrepresentable* rather than merely capped — the field evidence is four standing
agents consuming a weekly budget in twenty-one seconds.

``test_two_fires_in_one_window_share_an_id`` is what makes a duplicate harmless
rather than merely unlikely, since it is the effect ledger and not the scheduler's
carefulness that stops the second comment.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from ronin.retainer.model import Channel, SummonsKind
from ronin.retainer.routines import (
    MAX_ROUTINES_PER_RETAINER,
    MINIMUM_INTERVAL_SECONDS,
    ROUTINES_FILENAME,
    ROUTINES_SCHEMA_VERSION,
    Routine,
    RoutineError,
    RoutineStore,
    fire,
    sequence_of,
)

HOUR = 3600
MIDNIGHT = 1757116800.0


def routine(**kwargs: Any) -> Routine:
    base: dict[str, Any] = {
        "id": "nightly-ci",
        "retainer": "sentry",
        "channel": Channel.GITHUB,
        "thread": "rohithkandula19/Ronin#258",
        "prompt": "check whether CI is green and say so",
        "interval_s": HOUR,
    }
    base.update(kwargs)
    return Routine(**base)


def store(tmp_path: Path, **kwargs: Any) -> RoutineStore:
    return RoutineStore.open(tmp_path / "retainer", **kwargs)


# --------------------------------------------------------------------------- #
# No catch-up
# --------------------------------------------------------------------------- #


def test_a_machine_that_slept_for_a_week_fires_once(tmp_path: Path) -> None:
    """Overdue is not owed. 168 hourly windows passed; one run happens."""
    book = store(tmp_path)
    book.add(routine(last_fired=MIDNIGHT))
    a_week_later = MIDNIGHT + 7 * 24 * HOUR

    fired = 0
    while book.due(a_week_later):
        for each in book.due(a_week_later):
            fire(book, each, a_week_later)
            fired += 1
        if fired > 3:
            break  # a loop here would be the bug; the assertion below catches it
    assert fired == 1


def test_firing_advances_to_now_not_by_one_interval(tmp_path: Path) -> None:
    book = store(tmp_path)
    stored = book.add(routine(last_fired=MIDNIGHT))
    a_week_later = MIDNIGHT + 7 * 24 * HOUR
    _summons, advanced = fire(book, stored, a_week_later)
    assert advanced.last_fired == a_week_later
    assert not advanced.due(a_week_later)


def test_the_backlog_is_reported_but_never_acted_on(tmp_path: Path) -> None:
    """A Retainer should be able to say it missed a night, not silently pretend."""
    stored = routine(last_fired=MIDNIGHT)
    assert stored.overdue_by(MIDNIGHT + 5 * HOUR) == 4
    assert stored.overdue_by(MIDNIGHT + HOUR) == 0
    assert routine().overdue_by(MIDNIGHT) == 0


# --------------------------------------------------------------------------- #
# A routine cannot name another Retainer
# --------------------------------------------------------------------------- #


def test_a_routine_cannot_name_another_retainer() -> None:
    """Unrepresentable, not capped: there is no target field to set."""
    names = {f.name for f in fields(Routine)}
    assert "retainer" in names
    assert not (names & {"target", "targets", "notify", "to", "recipient"})


def test_a_fired_routine_summons_only_its_own_retainer() -> None:
    stored = routine(retainer="sentry")
    assert stored.summons(MIDNIGHT).retainer == "sentry"


def test_a_routine_retainer_must_be_a_slug() -> None:
    with pytest.raises(ValueError, match="slug"):
        routine(retainer="../escape")


# --------------------------------------------------------------------------- #
# One fire per window
# --------------------------------------------------------------------------- #


def test_two_fires_in_one_window_share_an_id() -> None:
    stored = routine()
    early = MIDNIGHT + 60
    late = MIDNIGHT + 600
    assert stored.window(early) == stored.window(late)
    assert stored.fire_id(early) == stored.fire_id(late)


def test_fires_in_different_windows_have_different_ids() -> None:
    stored = routine()
    assert stored.fire_id(MIDNIGHT) != stored.fire_id(MIDNIGHT + HOUR)


def test_two_routines_in_one_window_have_different_ids() -> None:
    assert routine(id="a").fire_id(MIDNIGHT) != routine(id="b").fire_id(MIDNIGHT)


def test_a_second_scheduler_cannot_fire_the_same_turn(tmp_path: Path) -> None:
    book = store(tmp_path)
    stored = book.add(routine())
    fire(book, stored, MIDNIGHT + HOUR)
    with pytest.raises(RoutineError, match="already fired"):
        fire(book, stored, MIDNIGHT + HOUR)


# --------------------------------------------------------------------------- #
# Due-ness
# --------------------------------------------------------------------------- #


def test_a_routine_that_has_never_run_is_due() -> None:
    assert routine().due(MIDNIGHT)


def test_a_routine_is_not_due_again_inside_its_window() -> None:
    stored = routine(last_fired=MIDNIGHT + 60)
    assert not stored.due(MIDNIGHT + 120)


def test_a_routine_is_due_in_the_next_window() -> None:
    stored = routine(last_fired=MIDNIGHT + 60)
    assert stored.due(MIDNIGHT + HOUR + 60)


def test_a_disabled_routine_is_never_due() -> None:
    assert not routine(enabled=False).due(MIDNIGHT + 10 * HOUR)


def test_disabling_keeps_when_it_last_ran(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.add(routine(last_fired=MIDNIGHT))
    off = book.enable("nightly-ci", enabled=False)
    assert off.last_fired == MIDNIGHT
    assert not off.enabled
    assert book.enable("nightly-ci").enabled


def test_enabling_something_that_does_not_exist_says_so(tmp_path: Path) -> None:
    with pytest.raises(RoutineError, match="no routine"):
        store(tmp_path).enable("nope")


def test_due_is_defined_once_and_the_query_does_not_reimplement_it(
    tmp_path: Path,
) -> None:
    """A WHERE clause duplicating due() is the copy that would drift."""
    book = store(tmp_path)
    book.add(routine(id="ready", last_fired=MIDNIGHT))
    book.add(routine(id="waiting", last_fired=MIDNIGHT + HOUR))
    book.add(routine(id="off", enabled=False))
    assert sequence_of(book.due(MIDNIGHT + HOUR + 60)) == ("ready",)


def test_due_can_be_narrowed_to_one_retainer(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.add(routine(id="a", retainer="sentry"))
    book.add(routine(id="b", retainer="scout"))
    assert sequence_of(book.due(MIDNIGHT, retainer="scout")) == ("b",)
    assert len(book.due(MIDNIGHT)) == 2


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #


def test_an_interval_below_the_floor_is_refused() -> None:
    with pytest.raises(ValueError, match="webhook instead"):
        routine(interval_s=60)
    assert routine(interval_s=MINIMUM_INTERVAL_SECONDS).interval_s == 300


def test_a_retainer_may_not_hold_unbounded_routines(tmp_path: Path) -> None:
    book = store(tmp_path)
    for index in range(MAX_ROUTINES_PER_RETAINER):
        book.add(routine(id=f"r{index:02d}"))
    with pytest.raises(RoutineError, match="chatter with extra steps"):
        book.add(routine(id="one-too-many"))


def test_the_cap_is_per_retainer_not_global(tmp_path: Path) -> None:
    book = store(tmp_path)
    for index in range(MAX_ROUTINES_PER_RETAINER):
        book.add(routine(id=f"r{index:02d}", retainer="sentry"))
    assert book.add(routine(id="scouts-first", retainer="scout")).retainer == "scout"


def test_a_routine_must_report_somewhere() -> None:
    with pytest.raises(ValueError, match="must report somewhere"):
        routine(thread="")


def test_a_routine_must_have_something_to_do() -> None:
    with pytest.raises(ValueError, match="nothing to do"):
        routine(prompt="   ")


def test_a_routine_needs_an_id() -> None:
    with pytest.raises(ValueError, match="id is required"):
        routine(id="")


# --------------------------------------------------------------------------- #
# The same Summons a mention makes
# --------------------------------------------------------------------------- #


def test_a_routines_summons_is_the_same_shape_as_a_mentions() -> None:
    from ronin.retainer.adapters import github

    mention = github.Mention(
        repo="o/n", number=1, actor="a", text="t", event="issues", action="opened"
    )
    from_mention = github.to_summons(mention, "sentry")
    from_routine = routine().summons(MIDNIGHT)
    assert tuple(f.name for f in fields(from_mention)) == tuple(
        f.name for f in fields(from_routine)
    )
    assert from_routine.kind is SummonsKind.ROUTINE
    assert from_mention.kind is SummonsKind.MENTION


def test_the_kind_is_provenance_and_not_a_privilege_level() -> None:
    """Nothing but the kind distinguishes the two, which is the safety argument."""
    from dataclasses import replace

    scheduled = routine().summons(MIDNIGHT)
    as_mention = replace(scheduled, kind=SummonsKind.MENTION, actor="somebody")
    assert (scheduled.retainer, scheduled.thread, scheduled.text) == (
        as_mention.retainer,
        as_mention.thread,
        as_mention.text,
    )


def test_the_actor_says_which_routine_rather_than_pretending_to_be_a_person() -> None:
    assert routine(id="nightly-ci").summons(MIDNIGHT).actor == "routine:nightly-ci"


def test_a_routine_summons_carries_no_escalation() -> None:
    assert routine().summons(MIDNIGHT).escalation == ""


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #


def test_a_duplicate_id_is_refused(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.add(routine())
    with pytest.raises(RoutineError, match="already exists"):
        book.add(routine())


def test_routines_survive_reopening(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.add(routine(last_fired=MIDNIGHT))
    again = RoutineStore.open(tmp_path / "retainer")
    found = again.lookup("nightly-ci")
    assert found is not None
    assert (found.channel, found.interval_s, found.last_fired) == (
        Channel.GITHUB,
        HOUR,
        MIDNIGHT,
    )


def test_removing_reports_whether_there_was_one(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.add(routine())
    assert book.remove("nightly-ci")
    assert not book.remove("nightly-ci")
    assert book.lookup("nightly-ci") is None


def test_of_lists_a_retainers_routines_enabled_or_not(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.add(routine(id="b"))
    book.add(routine(id="a", enabled=False))
    assert sequence_of(book.of("sentry")) == ("a", "b")
    assert book.of("nobody") == ()


def test_lookup_of_nothing_is_none(tmp_path: Path) -> None:
    assert store(tmp_path).lookup("nope") is None


# --------------------------------------------------------------------------- #
# Durability
# --------------------------------------------------------------------------- #


def test_a_schema_it_does_not_understand_is_refused(tmp_path: Path) -> None:
    book = store(tmp_path)
    book.add(routine())
    with sqlite3.connect(book.path) as conn:
        conn.execute(f"PRAGMA user_version={ROUTINES_SCHEMA_VERSION + 1}")
    with pytest.raises(RoutineError, match="worst way"):
        RoutineStore.open(tmp_path / "retainer")


def test_a_file_that_is_not_a_database_raises(tmp_path: Path) -> None:
    directory = tmp_path / "retainer"
    directory.mkdir()
    (directory / ROUTINES_FILENAME).write_bytes(b"not a database")
    with pytest.raises(RoutineError, match="cannot prepare"):
        RoutineStore.open(directory)


def test_a_path_that_cannot_be_opened_at_all_raises(tmp_path: Path) -> None:
    directory = tmp_path / "retainer"
    directory.mkdir()
    (directory / ROUTINES_FILENAME).mkdir()
    with pytest.raises(RoutineError, match="cannot open the routine store"):
        RoutineStore.open(directory)


def test_every_operation_raises_when_the_file_is_destroyed_under_us(
    tmp_path: Path,
) -> None:
    book = store(tmp_path)
    stored = book.add(routine())
    book.path.write_bytes(b"clobbered")
    with pytest.raises(RoutineError, match="cannot read"):
        book.lookup("nightly-ci")
    with pytest.raises(RoutineError, match="cannot read"):
        book.of("sentry")
    with pytest.raises(RoutineError, match="cannot read"):
        book.due(MIDNIGHT)
    with pytest.raises(RoutineError, match="cannot remove"):
        book.remove("nightly-ci")
    with pytest.raises(RoutineError, match="cannot mark"):
        book.mark_fired(stored, MIDNIGHT)


def test_adding_and_enabling_raise_when_the_file_is_destroyed(tmp_path: Path) -> None:
    """`add` and `enable` read first, so their writes fail separately."""
    book = store(tmp_path)

    class Clobbering(RoutineStore):
        def of(self, retainer: str) -> tuple[Routine, ...]:
            self.path.write_bytes(b"clobbered")
            return ()

    broken = Clobbering(path=book.path)
    with pytest.raises(RoutineError, match="cannot store"):
        broken.add(routine())
    with pytest.raises(RoutineError, match="cannot update"):
        broken.enable("nightly-ci")


def test_two_routines_with_identical_prompts_still_have_different_fire_ids() -> None:
    """The bug this pins: summons_id does not hash the actor, on purpose.

    Two routines differing only in id produce byte-identical summonses. Without
    the routine id in the nonce they shared a fire id, and the effect ledger
    would have suppressed one of them forever rather than visibly.
    """
    one = routine(id="nightly-ci")
    two = routine(id="nightly-lint")
    assert one.summons(MIDNIGHT).text == two.summons(MIDNIGHT).text
    assert one.summons(MIDNIGHT).thread == two.summons(MIDNIGHT).thread
    assert one.fire_id(MIDNIGHT) != two.fire_id(MIDNIGHT)


def test_the_actor_is_deliberately_absent_from_the_summons_id() -> None:
    """Stated as a test so the reason above cannot be "fixed" by adding it."""
    from dataclasses import replace

    from ronin.retainer.model import summons_id

    scheduled = routine().summons(MIDNIGHT)
    renamed = replace(scheduled, actor="somebody-else")
    assert summons_id(scheduled) == summons_id(renamed)
