"""Document processing: honest stdlib-only text extraction, chunking, redaction.

Only plaintext-family content types are handled, because they can be decoded
with the standard library alone and without guessing:

* ``text/plain``
* ``text/markdown``
* ``application/json``  (re-serialized deterministically)
* ``text/csv``          (normalized to whitespace-joined rows)

Binary formats that genuinely need third-party parser libraries (PDF, Office
documents, images) are *not* faked. Requesting them raises
:class:`UnsupportedDocumentType`; :data:`REQUIRES_PARSER_LIBS` documents which
types are blocked and why (:data:`BLOCKED_DEPENDENCY`).

A redaction pass runs before any text or chunk leaves this module, so callers
downstream never receive raw email addresses or long digit runs.
"""

from __future__ import annotations

import csv
import io
import json
import re

from .errors import UnsupportedDocumentType
from .models import Chunk, ExtractedDocument, StoredBlob

__all__ = [
    "TEXT_CONTENT_TYPES",
    "REQUIRES_PARSER_LIBS",
    "BLOCKED_DEPENDENCY",
    "EMAIL_PLACEHOLDER",
    "DIGITS_PLACEHOLDER",
    "extract_text",
    "chunk_text",
    "redact",
    "process_blob",
]

#: Content types this module can honestly extract with the stdlib alone.
TEXT_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "application/json",
        "text/csv",
    }
)

#: Label recorded when a required parser dependency is deliberately absent.
BLOCKED_DEPENDENCY: str = "BLOCKED_DEPENDENCY"

#: Types whose extraction needs a binary parser library that is not installed.
#: Requesting extraction for any of these raises :class:`UnsupportedDocumentType`.
REQUIRES_PARSER_LIBS: dict[str, str] = {
    "application/pdf": BLOCKED_DEPENDENCY,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        BLOCKED_DEPENDENCY
    ),
    "application/msword": BLOCKED_DEPENDENCY,
    "image/png": BLOCKED_DEPENDENCY,
    "image/jpeg": BLOCKED_DEPENDENCY,
    "image/tiff": BLOCKED_DEPENDENCY,
}

#: Replacement tokens used by the redaction pass. Generic, no real secrets.
EMAIL_PLACEHOLDER: str = "[REDACTED_EMAIL]"
DIGITS_PLACEHOLDER: str = "[REDACTED_DIGITS]"

# Generic PII/secret-shaped patterns. Kept intentionally broad and free of any
# real secret material so the security scanner sees only regex metacharacters.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Runs of 7 or more digits (optionally separated by spaces/dashes): phone
# numbers, card-like numbers, account ids, long tokens, etc.
_DIGITS_RE = re.compile(r"\b\d[\d\-\s]{5,}\d\b")


def _decode(data: bytes) -> str:
    """Decode bytes as UTF-8, failing closed on invalid input."""

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - exercised in tests
        raise UnsupportedDocumentType(
            "content is not valid UTF-8 text and cannot be extracted"
        ) from exc


def _extract_json(text: str) -> str:
    """Re-serialize JSON deterministically (sorted keys, stable separators)."""

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnsupportedDocumentType("content is not valid JSON") from exc
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(", ", ": "))


def _extract_csv(text: str) -> str:
    """Flatten CSV rows to whitespace-joined lines, deterministically."""

    reader = csv.reader(io.StringIO(text))
    lines = [" ".join(cell.strip() for cell in row) for row in reader]
    return "\n".join(lines)


def extract_text(blob: StoredBlob | bytes, content_type: str) -> str:
    """Extract UTF-8 text from a plaintext-family blob.

    Args:
        blob: A :class:`StoredBlob` or raw ``bytes``.
        content_type: The declared MIME type.

    Returns:
        The extracted (not yet redacted) text.

    Raises:
        UnsupportedDocumentType: If ``content_type`` requires a parser library
            that is not installed, or is otherwise unhandled, or the bytes are
            not decodable/parseable.
    """

    data = blob.data if isinstance(blob, StoredBlob) else bytes(blob)

    if content_type in REQUIRES_PARSER_LIBS:
        raise UnsupportedDocumentType(
            f"{content_type!r} requires a parser library ({BLOCKED_DEPENDENCY}); "
            "extraction is refused rather than faked"
        )
    if content_type not in TEXT_CONTENT_TYPES:
        raise UnsupportedDocumentType(f"unsupported content_type {content_type!r}")

    text = _decode(data)
    if content_type == "application/json":
        return _extract_json(text)
    if content_type == "text/csv":
        return _extract_csv(text)
    return text


def redact(text: str) -> tuple[str, bool]:
    """Strip obvious secret/PII-shaped patterns from ``text``.

    Returns the redacted text and a flag that is ``True`` when any substitution
    was made.
    """

    redacted = _EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)
    redacted = _DIGITS_RE.sub(DIGITS_PLACEHOLDER, redacted)
    return redacted, redacted != text


def chunk_text(text: str, *, size: int = 512, overlap: int = 64) -> list[Chunk]:
    """Split ``text`` into overlapping chunks with stable indices.

    Args:
        text: Source text.
        size: Maximum characters per chunk (must be positive).
        overlap: Characters of overlap between consecutive chunks; must be
            non-negative and strictly less than ``size``.

    Returns:
        A deterministic list of :class:`Chunk`. Empty text yields no chunks.
    """

    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")

    if not text:
        return []

    step = size - overlap
    chunks: list[Chunk] = []
    index = 0
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        chunks.append(Chunk(index=index, text=text[start:end], start=start, end=end))
        index += 1
        if end == length:
            break
        start += step
    return chunks


def process_blob(
    blob: StoredBlob | bytes,
    content_type: str,
    *,
    size: int = 512,
    overlap: int = 64,
) -> ExtractedDocument:
    """Extract, redact, then chunk a blob in one deterministic pass.

    Redaction runs on the extracted text *before* chunking, so no chunk can
    contain raw sensitive data.
    """

    raw = extract_text(blob, content_type)
    redacted_text, fired = redact(raw)
    chunks = chunk_text(redacted_text, size=size, overlap=overlap)
    return ExtractedDocument(
        content_type=content_type,
        text=redacted_text,
        redacted=fired,
        chunks=chunks,
    )
