"""Tests for document processing: extraction, chunking, redaction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ronin_storage import (
    BLOCKED_DEPENDENCY,
    DIGITS_PLACEHOLDER,
    EMAIL_PLACEHOLDER,
    REQUIRES_PARSER_LIBS,
    UnsupportedDocumentType,
    chunk_text,
    extract_text,
    process_blob,
    redact,
)
from ronin_storage import MemoryBlobStore

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def test_storage_extract_plain_text():
    assert extract_text(b"just text", "text/plain") == "just text"


def test_storage_extract_markdown():
    md = b"# Title\n\nBody text."
    assert extract_text(md, "text/markdown") == "# Title\n\nBody text."


def test_storage_extract_json_deterministic():
    a = extract_text(b'{"b": 1, "a": 2}', "application/json")
    b = extract_text(b'{"a": 2, "b": 1}', "application/json")
    assert a == b  # key order does not matter
    assert '"a": 2' in a


def test_storage_extract_csv_flattened():
    out = extract_text(b"name,age\nAda,36\n", "text/csv")
    assert out == "name age\nAda 36"


def test_storage_extract_invalid_json_raises():
    with pytest.raises(UnsupportedDocumentType):
        extract_text(b"{not json", "application/json")


def test_storage_extract_from_stored_blob():
    store = MemoryBlobStore()
    md = store.put("orgA", b"hello", "text/plain", now=NOW)
    blob = store.get("orgA", md.sha256)
    assert extract_text(blob, "text/plain") == "hello"


def test_storage_extract_pdf_refused():
    with pytest.raises(UnsupportedDocumentType) as exc:
        extract_text(b"%PDF-1.7 ...", "application/pdf")
    assert BLOCKED_DEPENDENCY in str(exc.value)


def test_storage_extract_docx_refused():
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with pytest.raises(UnsupportedDocumentType):
        extract_text(b"PK\x03\x04", ct)


def test_storage_extract_image_refused():
    with pytest.raises(UnsupportedDocumentType):
        extract_text(b"\x89PNG\r\n", "image/png")


def test_storage_requires_parser_libs_labels():
    assert REQUIRES_PARSER_LIBS["application/pdf"] == BLOCKED_DEPENDENCY
    assert all(v == BLOCKED_DEPENDENCY for v in REQUIRES_PARSER_LIBS.values())


def test_storage_extract_unknown_type_refused():
    with pytest.raises(UnsupportedDocumentType):
        extract_text(b"data", "application/x-unknown")


def test_storage_extract_non_utf8_refused():
    with pytest.raises(UnsupportedDocumentType):
        extract_text(b"\xff\xfe\x00bad", "text/plain")


def test_storage_redact_email():
    text = "contact person at user.name@example.com now"
    out, fired = redact(text)
    assert fired is True
    assert EMAIL_PLACEHOLDER in out
    assert "@example.com" not in out


def test_storage_redact_long_digits():
    text = "ref number 123456789012 end"
    out, fired = redact(text)
    assert fired is True
    assert DIGITS_PLACEHOLDER in out
    assert "123456789012" not in out


def test_storage_redact_no_match():
    out, fired = redact("nothing sensitive here, only words and 42")
    assert fired is False
    assert out == "nothing sensitive here, only words and 42"


def test_storage_chunk_basic_indices():
    chunks = chunk_text("abcdefghij", size=4, overlap=1)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # First chunk is the head of the text.
    assert chunks[0].text == "abcd"
    assert chunks[0].start == 0 and chunks[0].end == 4
    # Overlap of 1 => step of 3.
    assert chunks[1].start == 3


def test_storage_chunk_reconstructs_text():
    text = "the quick brown fox jumps over the lazy dog"
    chunks = chunk_text(text, size=10, overlap=3)
    # Non-overlapping prefix reconstruction.
    rebuilt = chunks[0].text
    for c in chunks[1:]:
        rebuilt += c.text[3:] if len(c.text) > 3 else ""
    assert rebuilt.startswith("the quick")


def test_storage_chunk_deterministic():
    text = "x" * 100
    a = chunk_text(text, size=30, overlap=5)
    b = chunk_text(text, size=30, overlap=5)
    assert [(c.index, c.start, c.end, c.text) for c in a] == [
        (c.index, c.start, c.end, c.text) for c in b
    ]


def test_storage_chunk_empty_text():
    assert chunk_text("", size=10, overlap=2) == []


def test_storage_chunk_invalid_params():
    with pytest.raises(ValueError):
        chunk_text("abc", size=0)
    with pytest.raises(ValueError):
        chunk_text("abc", size=5, overlap=5)
    with pytest.raises(ValueError):
        chunk_text("abc", size=5, overlap=-1)


def test_storage_process_blob_redacts_before_chunking():
    payload = b"email me at test.user@example.org please respond soon"
    doc = process_blob(payload, "text/plain", size=20, overlap=5)
    assert doc.redacted is True
    assert "@example.org" not in doc.text
    # No chunk leaks the raw email.
    assert all("@example.org" not in c.text for c in doc.chunks)
    assert doc.content_type == "text/plain"


def test_storage_process_blob_no_redaction_flag():
    doc = process_blob(b"plain safe words only", "text/plain", size=50, overlap=5)
    assert doc.redacted is False
    assert doc.text == "plain safe words only"


def test_storage_process_blob_refuses_binary():
    with pytest.raises(UnsupportedDocumentType):
        process_blob(b"%PDF", "application/pdf")
