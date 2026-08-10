"""Tests for invsync.diff."""

from invsync.diff import compute
from invsync.records import Record


def test_a_new_sku_is_added():
    diff = compute([], [Record(sku="a", name="A", qty=1)])
    assert [record.sku for record in diff.added] == ["a"]
    assert diff.updated == []
    assert diff.removed == []


def test_a_missing_sku_is_removed():
    diff = compute([Record(sku="A", name="A", qty=1)], [])
    assert diff.removed == ["A"]
