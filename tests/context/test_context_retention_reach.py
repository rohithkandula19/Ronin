"""The compaction escape valve, and whether a user can actually reach it.

Compaction folds the middle and keeps the most recent tool result per file path.
Retention used to be unbounded: it is what made "what did we edit in turn 3"
answerable in turn 200. The cost was that a session touching more unique files than
the window can hold landed *above* the trigger even after folding, and that one
oversized result was re-sent through every later fold for the rest of the session.

Both ceilings now have defaults and escalation is on, so the unbounded behaviour is
a configuration rather than the shipped one. **Every test here therefore states its
policy explicitly.** Relying on the defaults would make these tests assertions about
what the defaults happen to be, and they are about the mechanism: what the ceilings
do, what escalation surrenders, and that nothing is dropped without being named.

The reachability point the module was written for still stands: the ceilings are
settable from ``settings.json``, so the advice the over-budget note prints is
something a user can act on.

Everything here is offline: a scripted transcript and a fixed summarizer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from context_harness import fake_summarizer, scripted_session, transcript_text

from ronin.context.compaction import (
    DEFAULT_MAX_RETAINED_CHARS,
    DEFAULT_MAX_RETAINED_PATHS,
    CompactionPolicy,
    compact,
)
from ronin.safety.settings import PROJECT_SETTINGS, load_settings

# A window small enough that 200 unique retained paths cannot possibly fit.
TIGHT = 4_000


def _turn(path: str) -> int:
    """The turn number a scripted path came from, for ordering assertions."""
    return int("".join(char for char in path if char.isdigit()) or 0)


#: The pre-ceiling behaviour, stated rather than inherited. Every test that wants to
#: observe "keep everything" has to ask for it now, because it is no longer default.
UNBOUNDED: dict[str, object] = {
    "max_retained_paths": None,
    "max_retained_chars": None,
    "escalate_to_fit": False,
}


async def _fold(**policy_kwargs: object) -> object:
    """Fold a 200-turn session. Unbounded unless the caller says otherwise."""
    return await compact(
        scripted_session(200, marked_turn=3, marked_path="src/turn3.py"),
        policy=CompactionPolicy(
            context_window=TIGHT,
            **{**UNBOUNDED, **policy_kwargs},  # type: ignore[arg-type]
        ),
        summarizer=fake_summarizer,
    )


# --------------------------------------------------------------------------- #
# the default keeps every file and reports rather than dropping
# --------------------------------------------------------------------------- #


async def test_by_default_nothing_is_surrendered_even_when_it_does_not_fit() -> None:
    """The guarantee wins over the budget, and the budget problem is reported.

    Being above the trigger is recoverable — the trigger is a *fraction* of the
    window, so the transcript still sends. A dropped file is not recoverable.
    """
    result = await _fold()
    assert result.surrendered_paths == ()  # type: ignore[attr-defined]
    assert result.still_over_trigger  # type: ignore[attr-defined]
    assert "src/turn3.py" in transcript_text(result.messages)  # type: ignore[attr-defined]
    assert "SENTINEL turn 3" in transcript_text(result.messages)  # type: ignore[attr-defined]


async def test_a_bounded_path_count_is_honoured_when_the_user_sets_one() -> None:
    result = await _fold(max_retained_paths=5)
    assert len(result.retained_paths) == 5  # type: ignore[attr-defined]
    assert not result.still_over_trigger  # type: ignore[attr-defined]
    # And the cost of setting it is the documented one, not a surprise.
    assert "src/turn3.py" not in result.retained_paths  # type: ignore[attr-defined]


async def test_a_bounded_char_count_keeps_the_path_but_marks_the_cut() -> None:
    result = await _fold(max_retained_chars=40)
    assert "src/turn3.py" in result.retained_paths  # type: ignore[attr-defined]
    assert "elided" in transcript_text(result.messages)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# escalation, when it is asked for
# --------------------------------------------------------------------------- #


async def test_escalation_surrenders_the_oldest_paths_and_names_every_one() -> None:
    result = await _fold(escalate_to_fit=True)
    surrendered = result.surrendered_paths  # type: ignore[attr-defined]
    assert surrendered, "escalation was requested and the transcript did not fit"
    # Named, not counted: a caller told only that *something* went cannot tell the
    # model what it no longer knows.
    assert all(path.startswith("src/") for path in surrendered)
    assert set(surrendered).isdisjoint(result.retained_paths)  # type: ignore[attr-defined]


async def test_escalation_keeps_the_newest_paths_because_they_are_next() -> None:
    result = await _fold(escalate_to_fit=True)
    kept = result.retained_paths  # type: ignore[attr-defined]
    surrendered = result.surrendered_paths  # type: ignore[attr-defined]
    assert kept, "escalation must not surrender everything"
    # First-call order is preserved, so everything surrendered precedes everything kept.
    # (The very last turns are in the pinned tail, not in retention at all.)
    assert surrendered[-1] != kept[0]
    assert _turn(kept[-1]) > _turn(surrendered[-1])
    assert _turn(kept[0]) > _turn(surrendered[0])


async def test_escalation_gets_the_transcript_inside_the_window() -> None:
    result = await _fold(escalate_to_fit=True)
    assert result.token_estimate_after <= TIGHT  # type: ignore[attr-defined]


async def test_escalation_surrenders_nothing_when_everything_already_fits() -> None:
    # A short session: escalation is enabled but has no work to do, and must not
    # drop a path just because it was allowed to.
    result = await compact(
        scripted_session(30),
        # The path ceiling off, so `surrendered_paths` can only be escalation's
        # doing — the static ceiling reports through the same field.
        policy=CompactionPolicy(
            context_window=200_000, escalate_to_fit=True, max_retained_paths=None
        ),
        summarizer=fake_summarizer,
    )
    assert result.surrendered_paths == ()
    assert not result.still_over_trigger


async def test_a_floor_that_alone_exceeds_the_window_surrenders_nothing() -> None:
    """Dropping file context cannot fix a pinned tail that does not fit.

    The head, the summary and the pinned tail are a floor escalation cannot lower,
    so it declines rather than emptying retention for no gain.
    """
    result = await compact(
        scripted_session(200),
        policy=CompactionPolicy(context_window=60, escalate_to_fit=True, max_retained_paths=None),
        summarizer=fake_summarizer,
    )
    assert result.surrendered_paths == ()
    assert result.still_over_trigger


# --------------------------------------------------------------------------- #
# the knobs are reachable from a settings file — the actual bug
# --------------------------------------------------------------------------- #


def _settings(tmp_path: Path, payload: dict[str, object]) -> object:
    (tmp_path / PROJECT_SETTINGS).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / PROJECT_SETTINGS).write_text(json.dumps(payload), encoding="utf-8")
    return load_settings(home=tmp_path / "home", cwd=tmp_path)


def test_the_retention_ceilings_can_be_set_from_a_settings_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path, {"max_retained_paths": 12, "max_retained_chars": 4000})
    assert settings.max_retained_paths == 12  # type: ignore[attr-defined]
    assert settings.max_retained_chars == 4000  # type: ignore[attr-defined]
    assert settings.healthy  # type: ignore[attr-defined]


def test_the_settings_defaults_match_the_policy_defaults(tmp_path: Path) -> None:
    """Two places state these, and a silent disagreement between them would mean a
    session behaves one way from a settings file and another from the policy
    dataclass — with nothing to indicate which one is in force."""
    settings = _settings(tmp_path, {})
    assert settings.max_retained_paths == DEFAULT_MAX_RETAINED_PATHS  # type: ignore[attr-defined]
    assert settings.max_retained_chars == DEFAULT_MAX_RETAINED_CHARS  # type: ignore[attr-defined]
    assert settings.compaction_escalate is True  # type: ignore[attr-defined]

    policy = CompactionPolicy(context_window=TIGHT)
    assert policy.max_retained_paths == settings.max_retained_paths  # type: ignore[attr-defined]
    assert policy.max_retained_chars == settings.max_retained_chars  # type: ignore[attr-defined]
    assert policy.escalate_to_fit is settings.compaction_escalate  # type: ignore[attr-defined]


def test_null_is_accepted_as_no_ceiling_rather_than_rejected(tmp_path: Path) -> None:
    # Writing it explicitly must mean the same as omitting it, or a user cannot
    # override a lower layer back to unbounded.
    settings = _settings(tmp_path, {"max_retained_paths": None})
    assert settings.max_retained_paths is None  # type: ignore[attr-defined]
    assert settings.healthy  # type: ignore[attr-defined]


@pytest.mark.parametrize("bad", [0, -1, "twelve", 1.5])
def test_a_nonsense_ceiling_is_reported_not_silently_ignored(tmp_path: Path, bad: object) -> None:
    settings = _settings(tmp_path, {"max_retained_paths": bad})
    assert not settings.healthy  # type: ignore[attr-defined]
    assert any("max_retained_paths" in str(error) for error in settings.errors)  # type: ignore[attr-defined]


def test_escalation_is_opt_in_from_settings(tmp_path: Path) -> None:
    assert _settings(tmp_path, {"compaction_escalate": True}).compaction_escalate is True  # type: ignore[attr-defined]


def test_the_ceilings_report_where_they_came_from(tmp_path: Path) -> None:
    # `/doctor` prints provenance for every scalar; a knob that cannot say which
    # layer set it is one the user cannot debug.
    settings = _settings(tmp_path, {"max_retained_paths": 7})
    assert settings.source_of("max_retained_paths") == "project"  # type: ignore[attr-defined]
    assert settings.source_of("max_retained_chars") == "builtin"  # type: ignore[attr-defined]
    assert any("max_retained_paths = 7" in line for line in settings.provenance())  # type: ignore[attr-defined]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
