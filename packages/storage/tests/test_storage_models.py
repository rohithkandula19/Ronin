"""Tests for the pydantic models: strictness and metadata contract."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ronin_storage import BlobMetadata, Chunk, ExtractedDocument, MemoryBlobStore

NOW = datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc)


def test_storage_metadata_forbids_extra_fields():
    with pytest.raises(ValidationError):
        BlobMetadata(
            org_id="o",
            sha256="a" * 64,
            size=1,
            content_type="text/plain",
            created_at=NOW,
            surprise="nope",  # type: ignore[call-arg]
        )


def test_storage_metadata_is_frozen():
    md = BlobMetadata(
        org_id="o", sha256="a" * 64, size=1, content_type="text/plain", created_at=NOW
    )
    with pytest.raises(ValidationError):
        md.size = 999  # type: ignore[misc]


def test_storage_metadata_full_contract():
    store = MemoryBlobStore()
    md = store.put("acme", b"payload", "text/markdown", now=NOW)
    # Every required metadata field is populated per the spec.
    assert md.org_id == "acme"
    assert len(md.sha256) == 64
    assert md.size == len(b"payload")
    assert md.content_type == "text/markdown"
    assert md.created_at == NOW


def test_storage_chunk_forbids_extra():
    with pytest.raises(ValidationError):
        Chunk(index=0, text="x", start=0, end=1, extra=1)  # type: ignore[call-arg]


def test_storage_extracted_document_defaults():
    doc = ExtractedDocument(content_type="text/plain", text="hi", redacted=False)
    assert doc.chunks == []
