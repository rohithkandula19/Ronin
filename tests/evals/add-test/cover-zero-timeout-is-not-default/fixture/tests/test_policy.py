"""Tests for wait.policy."""

from wait.policy import DEFAULT_TIMEOUT, effective_timeout


def test_none_uses_the_default():
    assert effective_timeout(None) == DEFAULT_TIMEOUT


def test_a_positive_timeout_passes_through():
    assert effective_timeout(2.5) == 2.5
