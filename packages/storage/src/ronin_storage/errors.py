"""Exception hierarchy for :mod:`ronin_storage`.

Every error raised by this package derives from :class:`StorageError` so that
callers can fail closed on a single base type. Errors are honest: they name the
exact guard that fired rather than silently degrading.
"""

from __future__ import annotations

__all__ = [
    "StorageError",
    "BlobNotFoundError",
    "ContentTypeNotAllowedError",
    "BlobTooLargeError",
    "OrgQuotaExceededError",
    "ChecksumMismatchError",
    "UnsupportedDocumentType",
]


class StorageError(Exception):
    """Base class for all storage-related failures."""


class BlobNotFoundError(StorageError):
    """Raised when a key is not present for the requesting org.

    Cross-org access surfaces as this error: org A asking for org B's key is
    indistinguishable from a missing key, which prevents existence probing.
    """


class ContentTypeNotAllowedError(StorageError):
    """Raised when a blob's declared content type is not on the allowlist."""


class BlobTooLargeError(StorageError):
    """Raised when a single blob exceeds the configured per-blob byte limit."""


class OrgQuotaExceededError(StorageError):
    """Raised when storing a blob would exceed the per-org total-byte quota."""


class ChecksumMismatchError(StorageError):
    """Raised when stored bytes do not hash to their content-addressed key.

    Signals corruption or tampering on read; callers must treat the blob as
    unreadable rather than trusting the bytes.
    """


class UnsupportedDocumentType(StorageError):
    """Raised when text extraction is requested for a type needing a parser lib.

    The package refuses to fake extraction for binary formats (PDF, docx,
    images). See :data:`ronin_storage.docproc.REQUIRES_PARSER_LIBS`.
    """
