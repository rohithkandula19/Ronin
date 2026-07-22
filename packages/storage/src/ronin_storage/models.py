"""Pydantic models describing stored blobs and processed documents.

All models forbid extra fields (fail-closed on unexpected input) and are fully
type-hinted. Timestamps are always caller-supplied so behaviour is deterministic.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BlobMetadata",
    "StoredBlob",
    "Chunk",
    "ExtractedDocument",
]


class BlobMetadata(BaseModel):
    """Immutable metadata carried by every stored blob."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str = Field(..., min_length=1, description="Owning organization id.")
    sha256: str = Field(..., description="Lowercase hex sha256 of the bytes; also the key.")
    size: int = Field(..., ge=0, description="Size of the stored bytes in bytes.")
    content_type: str = Field(..., min_length=1, description="Declared MIME type.")
    created_at: datetime = Field(..., description="Caller-supplied creation timestamp.")


class StoredBlob(BaseModel):
    """A blob together with its metadata as returned by ``get``."""

    model_config = ConfigDict(extra="forbid")

    data: bytes = Field(..., description="Raw stored bytes.")
    metadata: BlobMetadata = Field(..., description="Content-addressed metadata.")


class Chunk(BaseModel):
    """One overlapping slice of extracted text with a stable index."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(..., ge=0, description="Zero-based position of the chunk.")
    text: str = Field(..., description="Chunk text (already redacted).")
    start: int = Field(..., ge=0, description="Start offset into the redacted source text.")
    end: int = Field(..., ge=0, description="End offset (exclusive) into the redacted source text.")


class ExtractedDocument(BaseModel):
    """Result of extracting (and redacting) text from a blob."""

    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(..., description="Content type the extraction handled.")
    text: str = Field(..., description="Full redacted text.")
    redacted: bool = Field(..., description="True if the redaction pass changed anything.")
    chunks: list[Chunk] = Field(default_factory=list, description="Overlapping chunks.")
