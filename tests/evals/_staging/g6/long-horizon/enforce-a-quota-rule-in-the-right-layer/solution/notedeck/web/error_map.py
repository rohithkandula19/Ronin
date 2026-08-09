"""The only file in the project that knows an HTTP status code.

The table is ordered most-specific-first and matched with ``isinstance``, so an
error that subclasses a mapped error needs its own entry above its parent to get
its own status.
"""

from __future__ import annotations

import re

from ..errors import (
    Conflict,
    NotedeckError,
    NotFound,
    PermissionDenied,
    QuotaExceeded,
    StorageError,
    ValidationError,
)

STATUS_TABLE: tuple[tuple[type[NotedeckError], int], ...] = (
    (NotFound, 404),
    (ValidationError, 422),
    (PermissionDenied, 403),
    # More specific than Conflict, so it has to sit above it.
    (QuotaExceeded, 429),
    (Conflict, 409),
    (StorageError, 503),
)

DEFAULT_STATUS = 500

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def status_for(error: NotedeckError) -> int:
    for error_type, status in STATUS_TABLE:
        if isinstance(error, error_type):
            return status
    return DEFAULT_STATUS


def code_for(error: NotedeckError) -> str:
    """``QuotaExceeded`` -> ``quota_exceeded``; the code clients switch on."""
    return _CAMEL.sub("_", type(error).__name__).lower()


def to_body(error: NotedeckError) -> dict[str, str]:
    return {"error": code_for(error), "message": str(error)}
