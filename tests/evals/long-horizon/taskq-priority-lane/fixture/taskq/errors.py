"""Every exception this package raises, so callers can catch one base."""

from __future__ import annotations


class TaskqError(Exception):
    """Base for everything taskq raises."""


class ConfigError(TaskqError):
    """The configuration text could not be used."""


class UnknownJob(TaskqError):
    """No job with that id is in the store."""
