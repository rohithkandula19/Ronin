"""Prepend a licence header, at most once."""

from __future__ import annotations

HEADER = "# SPDX-License-Identifier: MIT\n"


def apply(text: str) -> str:
    """Ensure ``text`` starts with ``HEADER``. Idempotent by design."""
    if text.startswith(HEADER):
        return text
    return HEADER + text
