"""Page arithmetic for the list endpoints.

Pages are 1-based because that is what the public API promises; offsets are
0-based because that is what the storage layer takes.
"""


def page_count(total_items: int, per_page: int) -> int:
    """Return how many pages *total_items* needs at *per_page* items per page."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    if total_items <= 0:
        return 0
    return (total_items + per_page - 1) // per_page


def slice_for(total_items: int, per_page: int, page: int) -> tuple[int, int]:
    """Return the half-open (start, stop) offsets of 1-based *page*."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    if page < 1:
        raise ValueError("page numbers are 1-based")
    end = max(total_items, 0)
    start = min((page - 1) * per_page, end)
    return start, min(start + per_page, end)
