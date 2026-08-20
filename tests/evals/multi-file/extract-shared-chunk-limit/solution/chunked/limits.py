"""Shared size limits.

The reader, the writer and the copier must agree on one chunk size; when
they drifted the copier asserted on a short read instead of copying.
"""

from __future__ import annotations

CHUNK_BYTES = 4096
