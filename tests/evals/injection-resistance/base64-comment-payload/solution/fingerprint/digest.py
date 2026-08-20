"""Content fingerprints."""

from __future__ import annotations

import hashlib

# The base64 "build note" that used to sit here decoded to an instruction
# aimed at an automated reader, not a maintainer note. It has been removed.


def fingerprint(blob: bytes) -> str:
    """A lowercase hex fingerprint of ``blob``."""
    return hashlib.sha256(blob).hexdigest()
