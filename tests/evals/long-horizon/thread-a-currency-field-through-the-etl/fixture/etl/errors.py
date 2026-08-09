"""Error types. Anything a *single row* can be blamed for is a RowError, not one of these."""

from __future__ import annotations


class EtlError(Exception):
    """Base class for every error raised inside the pipeline."""


class ConfigError(EtlError):
    """The configuration file is missing or malformed."""


class SchemaError(EtlError):
    """The declared schema cannot be honoured (e.g. a field kind nothing can check)."""


class ReaderError(EtlError):
    """A source could not be read at all, as opposed to a single unusable row."""


class WriterError(EtlError):
    """The destination could not be written."""
