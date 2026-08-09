"""Domain errors.

Services raise these; only ``notedeck/web/error_map.py`` turns them into status
codes. Nothing here imports anything from ``notedeck.web``.
"""

from __future__ import annotations


class NotedeckError(Exception):
    """Base class for every error a caller could be told about."""


class NotFound(NotedeckError):
    """A record the caller named does not exist."""


class ValidationError(NotedeckError):
    """The caller sent something the domain cannot accept."""


class PermissionDenied(NotedeckError):
    """The caller may not touch this record."""


class Conflict(NotedeckError):
    """The request contradicts the current state of a record."""


class QuotaExceeded(Conflict):
    """The caller is already holding as much as their plan allows."""


class StorageError(NotedeckError):
    """The repository could not complete an operation."""
