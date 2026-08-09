"""CLI-level errors. A command never raises these; it returns a non-zero code."""

from __future__ import annotations


class AtticusError(Exception):
    """Base class for anything the CLI should report as a clean failure."""


class UsageError(AtticusError):
    """The invocation does not make sense."""


class ConfigFileError(AtticusError):
    """config/atticus.json cannot be used."""
