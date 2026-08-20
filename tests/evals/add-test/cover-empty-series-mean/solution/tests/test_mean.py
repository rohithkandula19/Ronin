"""Tests for series.mean."""

from series.mean import mean


def test_mean_of_three_samples():
    assert mean([1.0, 2.0, 6.0]) == 3.0


def test_mean_of_an_empty_series_is_zero():
    assert mean([]) == 0.0
