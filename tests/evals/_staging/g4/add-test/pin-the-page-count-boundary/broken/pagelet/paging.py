"""Page arithmetic for the list endpoints."""


def page_count(total_items: int, per_page: int) -> int:
    """Return how many pages *total_items* needs at *per_page* items per page."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    if total_items <= 0:
        return 0
    return total_items // per_page + 1


def slice_for(total_items: int, per_page: int, page: int) -> tuple[int, int]:
    """Return the half-open (start, stop) offsets of 1-based *page*."""
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    if page < 1:
        raise ValueError("page numbers are 1-based")
    end = max(total_items, 0)
    start = min((page - 1) * per_page, end)
    return start, min(start + per_page, end)
