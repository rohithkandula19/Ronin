"""Tests for feed.load."""

from feed.load import load_records


def test_the_feed_loads():
    assert len(load_records()) >= 1
