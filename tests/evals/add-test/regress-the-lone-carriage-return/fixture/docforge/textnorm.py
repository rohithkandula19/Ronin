"""Normalise the text of an uploaded document.

Line endings arrive as CRLF from Windows editors and, from a few old exports,
as bare CR. The diff view downstream assumes LF, so everything is collapsed
here rather than in five places later.
"""


def normalize_newlines(text: str) -> str:
    """Return *text* with every line ending collapsed to a single LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_bytes(data: bytes) -> bytes:
    """Byte-level twin of :func:`normalize_newlines`, for binary reads."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def ensure_trailing_newline(text: str) -> str:
    """Return *text* with exactly one trailing LF, leaving "" alone."""
    if not text:
        return text
    return text if text.endswith("\n") else text + "\n"
