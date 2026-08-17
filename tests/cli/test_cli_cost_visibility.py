"""Cost and context visibility: the ledger, wired at last, and a warning before the wall.

The session audit found a full per-request ledger — cache-hit rate, main-versus-subagent
split, a price table for providers that report no cost — sitting in the provider layer and
**never constructed by the CLI**, so ``/cost`` could only report the coarse ``Budget``
totals. It also found the context gauge reporting a number with no warning attached, while
compaction folds the window silently at 80%.

These tests cover the two halves: what the ledger can now report, and that the gauge warns
before the fold rather than after it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ronin.providers.accounting import Ledger
from ronin.providers.router import ModelSpec, Role
from ronin.providers.types import Usage
from ronin.ui.render import CONTEXT_WARN_FRACTION, CONTEXT_WARN_MARK, render_status

MAIN = ModelSpec(name="main", provider="anthropic", model="claude-sonnet-4")
FAST = ModelSpec(name="fast", provider="anthropic", model="claude-haiku-4")


def _ledger(root: Path) -> Ledger:
    return Ledger(root / "ledger.db")


def _record(ledger: Ledger, *, role: Role, spec: ModelSpec, rid: str, **usage: object) -> None:
    ledger.record(
        session_id="s1",
        request_id=rid,
        role=role,
        spec=spec,
        usage=Usage(**usage),  # type: ignore[arg-type]
        prefix_fingerprint="fp",
    )


# --------------------------------------------------------------------------- #
# what the ledger can report that Budget cannot
# --------------------------------------------------------------------------- #


def test_the_ledger_reports_a_cache_hit_rate() -> None:
    with tempfile.TemporaryDirectory() as td:
        ledger = _ledger(Path(td))
        _record(
            ledger,
            role=Role.MAIN,
            spec=MAIN,
            rid="r1",
            input_tokens=9000,
            output_tokens=600,
            cache_read_tokens=7000,
            cache_write_tokens=1200,
            cost_usd=0.031,
        )
        totals = ledger.session_totals("s1")
        assert totals.requests == 1
        assert totals.cache_hit_rate > 0.0  # the number Budget structurally cannot produce
        assert "cache" in totals.summary()


def test_the_ledger_splits_main_from_the_cheap_role() -> None:
    """This is how "the expensive model did the cheap work" becomes visible."""
    with tempfile.TemporaryDirectory() as td:
        ledger = _ledger(Path(td))
        _record(ledger, role=Role.MAIN, spec=MAIN, rid="r1", input_tokens=9000, cost_usd=0.031)
        _record(ledger, role=Role.FAST, spec=FAST, rid="r2", input_tokens=2000, cost_usd=0.0009)
        roles = ledger.role_totals("s1")
        assert set(roles) == {Role.MAIN, Role.FAST}
        assert roles[Role.MAIN].cost_usd > roles[Role.FAST].cost_usd


def test_a_session_with_no_requests_has_nothing_to_break_down() -> None:
    # `/cost` falls back to the budget line alone rather than printing an empty table.
    with tempfile.TemporaryDirectory() as td:
        assert _ledger(Path(td)).session_totals("s1").requests == 0


def test_recording_the_same_request_twice_does_not_double_the_bill() -> None:
    # The natural retry path re-records; a doubled cost would be a silently wrong bill.
    with tempfile.TemporaryDirectory() as td:
        ledger = _ledger(Path(td))
        for _ in range(2):
            _record(ledger, role=Role.MAIN, spec=MAIN, rid="same", input_tokens=1000, cost_usd=0.01)
        totals = ledger.session_totals("s1")
        assert totals.requests == 1
        assert abs(totals.cost_usd - 0.01) < 1e-9


def test_the_summary_names_its_own_caveats_rather_than_rounding_them_away() -> None:
    with tempfile.TemporaryDirectory() as td:
        ledger = _ledger(Path(td))
        # A provider that reported no usage at all — counted, not silently averaged in.
        ledger.record(
            session_id="s1",
            request_id="r1",
            role=Role.MAIN,
            spec=MAIN,
            usage=Usage(),
            reported=False,
        )
        summary = ledger.session_totals("s1").summary()
        assert "no usage reported" in summary


def test_sessions_are_kept_apart() -> None:
    with tempfile.TemporaryDirectory() as td:
        ledger = _ledger(Path(td))
        _record(ledger, role=Role.MAIN, spec=MAIN, rid="r1", input_tokens=500, cost_usd=0.02)
        assert ledger.session_totals("other").requests == 0
        assert ledger.session_totals("s1").requests == 1


# --------------------------------------------------------------------------- #
# the context gauge warns before the wall
# --------------------------------------------------------------------------- #


def _status(fraction: float) -> str:
    return render_status(model="m", context_used=fraction, cost_usd=0.0, cwd=".", branch="b")


def test_a_comfortable_context_carries_no_warning() -> None:
    assert CONTEXT_WARN_MARK not in _status(0.42)


def test_the_warning_appears_before_compaction_would_fire() -> None:
    # Compaction folds the window at 0.8; a warning that arrived after that would be
    # reporting history rather than giving the user a chance to act.
    assert CONTEXT_WARN_FRACTION < 0.8
    assert CONTEXT_WARN_MARK in _status(CONTEXT_WARN_FRACTION)
    assert CONTEXT_WARN_MARK not in _status(CONTEXT_WARN_FRACTION - 0.01)


def test_the_warning_persists_as_the_window_fills() -> None:
    for fraction in (0.8, 0.95, 1.0):
        assert CONTEXT_WARN_MARK in _status(fraction)


def test_the_percentage_is_still_reported_alongside_the_warning() -> None:
    shown = _status(0.83)
    assert "83%" in shown  # the warning augments the number, it does not replace it
