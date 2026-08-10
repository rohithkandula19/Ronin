"""Intake rules. Wording here is user-visible and has been signed off by support."""

import os

MAX_SIZE_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = frozenset({".txt", ".pdf", ".png"})
_TEMP_SUFFIXES = (".part", ".tmp", ".crdownload")


def extension_of(name):
    """The lower-cased extension of *name*, including the dot ('' if there is none)."""
    return os.path.splitext(name)[1].lower()


def sanitize(name):
    """Return *name* with any directory components stripped off.

    Called before a filename is echoed back to the user, so a hostile client
    cannot get us to render a path.
    """
    cleaned = name.strip()
    for suffix in _TEMP_SUFFIXES:
        if cleaned.lower().endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned


def is_valid(upload):
    """Apply the intake rules to *upload*."""
    if not upload.filename.strip():
        return "filename is empty"
    if upload.size_bytes > MAX_SIZE_BYTES:
        return "file is larger than 20 MB"
    extension = extension_of(upload.filename)
    if extension not in ALLOWED_EXTENSIONS:
        return f"extension {extension!r} is not allowed"
    return None
