"""Ronin Storage.

Per-org, content-addressed object storage with fail-closed guards, plus honest
stdlib-only document processing (extraction, chunking, redaction).

Nothing here touches the network or any cloud SDK. The local backend writes
only beneath a caller-supplied root path, timestamps are always caller-supplied,
and blob keys are content-addressed (sha256) rather than random -- so behaviour
is fully deterministic.
"""

from __future__ import annotations

from .blobstore import (
    DEFAULT_ALLOWED_CONTENT_TYPES,
    BlobStore,
    LocalBlobStore,
    MemoryBlobStore,
    StorageGuards,
    sha256_hex,
)
from .docproc import (
    BLOCKED_DEPENDENCY,
    DIGITS_PLACEHOLDER,
    EMAIL_PLACEHOLDER,
    REQUIRES_PARSER_LIBS,
    TEXT_CONTENT_TYPES,
    chunk_text,
    extract_text,
    process_blob,
    redact,
)
from .errors import (
    BlobNotFoundError,
    BlobTooLargeError,
    ChecksumMismatchError,
    ContentTypeNotAllowedError,
    OrgQuotaExceededError,
    StorageError,
    UnsupportedDocumentType,
)
from .models import BlobMetadata, Chunk, ExtractedDocument, StoredBlob

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # blob store
    "BlobStore",
    "MemoryBlobStore",
    "LocalBlobStore",
    "StorageGuards",
    "DEFAULT_ALLOWED_CONTENT_TYPES",
    "sha256_hex",
    # models
    "BlobMetadata",
    "StoredBlob",
    "Chunk",
    "ExtractedDocument",
    # docproc
    "extract_text",
    "chunk_text",
    "redact",
    "process_blob",
    "TEXT_CONTENT_TYPES",
    "REQUIRES_PARSER_LIBS",
    "BLOCKED_DEPENDENCY",
    "EMAIL_PLACEHOLDER",
    "DIGITS_PLACEHOLDER",
    # errors
    "StorageError",
    "BlobNotFoundError",
    "ContentTypeNotAllowedError",
    "BlobTooLargeError",
    "OrgQuotaExceededError",
    "ChecksumMismatchError",
    "UnsupportedDocumentType",
]
