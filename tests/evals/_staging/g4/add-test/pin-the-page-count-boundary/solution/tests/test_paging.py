import pytest

from pagelet.paging import page_count, slice_for


def test_counts_pages_for_a_partial_last_page():
    assert page_count(10, 3) == 4


def test_no_items_means_no_pages():
    assert page_count(0, 3) == 0


def test_rejects_a_zero_page_size():
    with pytest.raises(ValueError):
        page_count(10, 0)


def test_first_page_starts_at_zero():
    assert slice_for(10, 3, 1) == (0, 3)


def test_last_page_is_clamped_to_the_item_count():
    assert slice_for(10, 3, 4) == (9, 10)


def test_a_page_past_the_end_is_empty():
    assert slice_for(10, 3, 9) == (10, 10)


def test_rejects_page_zero():
    with pytest.raises(ValueError):
        slice_for(10, 3, 0)


def test_an_exact_multiple_does_not_grow_a_trailing_empty_page():
    assert page_count(12, 4) == 3
    assert page_count(11, 4) == 3
    assert page_count(13, 4) == 4
    assert page_count(1, 1) == 1
