"""Normalise the text of an uploaded document."""


def normalize_newlines(text: str) -> str:
    """Return *text* with CRLF line endings collapsed to LF."""
    return text.replace("\r\n", "\n")


def normalize_bytes(data: bytes) -> bytes:
    """Byte-level twin of :func:`normalize_newlines`, for binary reads."""
    return data.replace(b"\r\n", b"\n")


def ensure_trailing_newline(text: str) -> str:
    """Return *text* with exactly one trailing LF, leaving "" alone."""
    if not text:
        return text
    return text if text.endswith("\n") else text + "\n"
