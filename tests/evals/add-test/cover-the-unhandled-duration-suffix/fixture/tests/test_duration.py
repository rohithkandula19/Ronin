import pytest

from timekit.duration import DurationError, format_duration, parse_duration


def test_parses_seconds():
    assert parse_duration("90s") == 90


def test_parses_minutes():
    assert parse_duration("15m") == 900


def test_parses_hours_and_days():
    assert parse_duration("2h") == 7200
    assert parse_duration("1d") == 86400


def test_ignores_surrounding_whitespace_and_case():
    assert parse_duration(" 5M ") == 300


def test_rejects_an_empty_duration():
    with pytest.raises(DurationError):
        parse_duration("   ")


def test_formats_with_the_largest_exact_unit():
    assert format_duration(7200) == "2h"
    assert format_duration(90) == "90s"
    assert format_duration(0) == "0s"
