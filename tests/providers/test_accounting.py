"""The ledger: persisted, idempotent, and honest about what it does not know.

The two distinctions under test are the ones that make a budget trustworthy:

* ``unpriced`` — we do not know what it cost, versus it cost nothing.
* ``unreported`` — the provider did not say how many tokens, versus zero tokens.

Collapsing either one gives a session summary that reads as "$0.00, 0 tokens" for a
run that in fact spent real money on a model nobody priced.
"""

from __future__ import annotations

from pathlib import Path

from ronin.providers import Ledger, ModelSpec, Role, Usage, price
from ronin.providers.accounting import Totals, sum_usage

PRICED = ModelSpec(
    name="kimi-k2",
    provider="moonshot",
    model="kimi-k2-0711-preview",
    price_in=0.60,
    price_out=2.50,
    price_cache_read=0.15,
)
UNPRICED = ModelSpec(name="local", provider="mlx", model="qwen")


def ledger(tmp_path: Path, *, at: float = 1_700_000_000.0) -> Ledger:
    return Ledger(tmp_path / "usage.db", clock=lambda: at)


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #


def test_a_priced_model_is_charged_per_million_tokens() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert price(usage, PRICED) == 0.60 + 2.50


def test_cached_reads_are_charged_at_the_cache_rate_when_config_gives_one() -> None:
    usage = Usage(cache_read_tokens=1_000_000)
    assert price(usage, PRICED) == 0.15


def test_cached_reads_fall_back_to_the_input_rate_when_no_cache_rate_is_set() -> None:
    """The conservative direction: never invent a discount the provider did not give."""
    spec = ModelSpec(name="x", provider="openai", model="m", price_in=1.0, price_out=2.0)
    assert price(Usage(cache_read_tokens=1_000_000), spec) == 1.0


def test_cache_writes_are_charged_at_the_input_rate() -> None:
    assert price(Usage(cache_write_tokens=1_000_000), PRICED) == 0.60


def test_an_unpriced_model_costs_zero() -> None:
    assert price(Usage(input_tokens=999_999), UNPRICED) == 0.0


def test_a_provider_reported_cost_wins_over_our_price_table() -> None:
    usage = Usage(input_tokens=1_000_000, cost_usd=0.42)
    assert price(usage, PRICED) == 0.42


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def test_a_recorded_request_survives_reopening_the_database(tmp_path: Path) -> None:
    first = ledger(tmp_path)
    first.record(
        session_id="s1",
        request_id="r1",
        role=Role.MAIN,
        spec=PRICED,
        usage=Usage(input_tokens=1000, output_tokens=500),
    )
    reopened = Ledger(tmp_path / "usage.db")
    assert reopened.session_totals("s1").requests == 1


def test_the_parent_directory_is_created(tmp_path: Path) -> None:
    Ledger(tmp_path / "nested" / "deeper" / "usage.db")
    assert (tmp_path / "nested" / "deeper" / "usage.db").exists()


def test_recording_the_same_request_twice_does_not_double_count(tmp_path: Path) -> None:
    """A reconnect that re-records must not inflate the bill."""
    book = ledger(tmp_path)
    for _ in range(3):
        book.record(
            session_id="s1",
            request_id="r1",
            role=Role.MAIN,
            spec=PRICED,
            usage=Usage(input_tokens=1_000_000, output_tokens=0),
        )
    totals = book.session_totals("s1")
    assert totals.requests == 1
    assert totals.cost_usd == 0.60


def test_a_re_record_updates_the_row_rather_than_ignoring_it(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(
        session_id="s1", request_id="r1", role=Role.MAIN, spec=PRICED, usage=Usage(input_tokens=1)
    )
    book.record(
        session_id="s1",
        request_id="r1",
        role=Role.MAIN,
        spec=PRICED,
        usage=Usage(input_tokens=999),
    )
    assert book.session_totals("s1").input_tokens == 999


def test_the_same_request_id_in_two_sessions_is_two_rows(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    for session in ("s1", "s2"):
        book.record(
            session_id=session,
            request_id="r1",
            role=Role.MAIN,
            spec=PRICED,
            usage=Usage(input_tokens=10),
        )
    assert book.totals().requests == 2
    assert book.session_totals("s1").requests == 1


# --------------------------------------------------------------------------- #
# Honesty
# --------------------------------------------------------------------------- #


def test_an_unpriced_request_is_flagged_not_reported_as_free(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(
        session_id="s1",
        request_id="r1",
        role=Role.FAST,
        spec=UNPRICED,
        usage=Usage(input_tokens=5000, output_tokens=1000),
    )
    totals = book.session_totals("s1")
    assert totals.cost_usd == 0.0
    assert totals.unpriced_requests == 1
    assert totals.input_tokens == 5000
    assert "unpriced" in totals.summary()


def test_a_request_with_no_reported_usage_is_flagged(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(
        session_id="s1",
        request_id="r1",
        role=Role.FAST,
        spec=UNPRICED,
        usage=Usage(),
        reported=False,
    )
    totals = book.session_totals("s1")
    assert totals.unreported_requests == 1
    assert "no usage reported" in totals.summary()


def test_an_unpriced_model_that_reports_its_own_cost_counts_as_priced(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(
        session_id="s1",
        request_id="r1",
        role=Role.MAIN,
        spec=UNPRICED,
        usage=Usage(cost_usd=0.05),
    )
    totals = book.session_totals("s1")
    assert totals.unpriced_requests == 0
    assert totals.cost_usd == 0.05


def test_a_clean_summary_mentions_no_caveats(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(
        session_id="s1",
        request_id="r1",
        role=Role.MAIN,
        spec=PRICED,
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    summary = book.session_totals("s1").summary()
    assert "unpriced" not in summary
    assert "no usage reported" not in summary


# --------------------------------------------------------------------------- #
# Per-role breakdown — where a routing mistake becomes visible
# --------------------------------------------------------------------------- #


def test_the_role_breakdown_shows_which_role_spent_the_money(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(
        session_id="s1",
        request_id="main-1",
        role=Role.MAIN,
        spec=PRICED,
        usage=Usage(input_tokens=1_000_000),
    )
    for index in range(20):
        book.record(
            session_id="s1",
            request_id=f"fast-{index}",
            role=Role.FAST,
            spec=UNPRICED,
            usage=Usage(input_tokens=2000),
        )
    breakdown = book.role_totals("s1")
    assert set(breakdown) == {Role.MAIN, Role.FAST}
    assert breakdown[Role.FAST].requests == 20
    assert breakdown[Role.FAST].cost_usd == 0.0
    assert breakdown[Role.MAIN].cost_usd == 0.60


def test_a_role_with_no_requests_is_absent_from_the_breakdown(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(session_id="s1", request_id="r1", role=Role.MAIN, spec=PRICED, usage=Usage())
    assert Role.PLAN not in book.role_totals("s1")


def test_an_empty_ledger_reports_zeroes_rather_than_raising(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    totals = book.session_totals("nothing-here")
    assert totals == Totals()
    assert totals.cache_hit_rate == 0.0
    assert book.sessions() == ()


# --------------------------------------------------------------------------- #
# Cache measurement, persisted
# --------------------------------------------------------------------------- #


def test_the_cache_hit_rate_is_computed_from_persisted_rows(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(
        session_id="s1",
        request_id="r1",
        role=Role.MAIN,
        spec=PRICED,
        usage=Usage(input_tokens=100, cache_read_tokens=900),
    )
    assert book.session_totals("s1").cache_hit_rate == 0.9


def test_many_distinct_prefixes_in_one_session_are_visible(tmp_path: Path) -> None:
    """The diagnosis for 'caching is on and the bill did not move'."""
    book = ledger(tmp_path)
    for index in range(5):
        book.record(
            session_id="s1",
            request_id=f"r{index}",
            role=Role.MAIN,
            spec=PRICED,
            usage=Usage(input_tokens=1000),
            prefix_fingerprint=f"fp{index}",
        )
    fingerprints = book.prefix_fingerprints("s1")
    assert len(fingerprints) == 5
    assert all(count == 1 for count in fingerprints.values())


def test_a_stable_prefix_shows_as_one_fingerprint_reused(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    for index in range(5):
        book.record(
            session_id="s1",
            request_id=f"r{index}",
            role=Role.MAIN,
            spec=PRICED,
            usage=Usage(input_tokens=100, cache_read_tokens=900),
            prefix_fingerprint="stable",
        )
    assert book.prefix_fingerprints("s1") == {"stable": 5}


def test_rows_with_no_fingerprint_are_omitted_from_the_breakdown(tmp_path: Path) -> None:
    book = ledger(tmp_path)
    book.record(session_id="s1", request_id="r1", role=Role.MAIN, spec=PRICED, usage=Usage())
    assert book.prefix_fingerprints("s1") == {}


def test_sessions_are_listed_most_recent_first(tmp_path: Path) -> None:
    path = tmp_path / "usage.db"
    for index, session in enumerate(("older", "newer")):
        book = Ledger(path, clock=lambda index=index: 1000.0 + index)  # type: ignore[misc]
        book.record(
            session_id=session,
            request_id="r1",
            role=Role.MAIN,
            spec=PRICED,
            usage=Usage(),
        )
    assert Ledger(path).sessions() == ("newer", "older")


# --------------------------------------------------------------------------- #
# Usage arithmetic
# --------------------------------------------------------------------------- #


def test_usages_add_field_by_field() -> None:
    total = Usage(input_tokens=1, output_tokens=2, cache_read_tokens=3) + Usage(
        input_tokens=10, output_tokens=20, cache_write_tokens=30, cost_usd=0.5
    )
    assert total == Usage(
        input_tokens=11,
        output_tokens=22,
        cache_read_tokens=3,
        cache_write_tokens=30,
        cost_usd=0.5,
    )


def test_sum_usage_folds_an_iterable() -> None:
    assert sum_usage([Usage(input_tokens=1)] * 3).input_tokens == 3


def test_sum_usage_of_nothing_is_empty() -> None:
    assert sum_usage([]) == Usage()


def test_total_tokens_excludes_cache_fields() -> None:
    """`total_tokens` is what was billed at the full rate, not everything sent."""
    usage = Usage(input_tokens=10, output_tokens=5, cache_read_tokens=1000)
    assert usage.total_tokens == 15
    assert usage.cacheable_input == 1000


def test_negative_token_counts_are_rejected() -> None:
    import pytest

    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        with pytest.raises(ValueError, match=f"{field} must be >= 0"):
            Usage(**{field: -1})


def test_a_negative_cost_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="cost_usd must be >= 0"):
        Usage(cost_usd=-0.01)
