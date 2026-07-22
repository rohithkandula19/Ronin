"""Content-addressed blob storage with per-org isolation and fail-closed guards.

Two backends are provided:

* :class:`MemoryBlobStore` -- keeps everything in a process-local dict.
* :class:`LocalBlobStore` -- writes under a single caller-supplied root path.

Both share identical semantics via :class:`BlobStore`. Keys are the lowercase
hex sha256 of the content, so storage is deduplicating and deterministic; no
randomness or network access is involved.

Guards (all fail-closed):

* a content-type allowlist -- disallowed types are rejected on ``put``;
* a per-blob maximum size -- oversize blobs are rejected on ``put``;
* a per-org total-byte quota -- a ``put`` that would exceed it is rejected;
* checksum verification on ``get`` -- corrupted bytes raise rather than return.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from .errors import (
    BlobNotFoundError,
    BlobTooLargeError,
    ChecksumMismatchError,
    ContentTypeNotAllowedError,
    OrgQuotaExceededError,
)
from .models import BlobMetadata, StoredBlob

__all__ = [
    "DEFAULT_ALLOWED_CONTENT_TYPES",
    "StorageGuards",
    "BlobStore",
    "MemoryBlobStore",
    "LocalBlobStore",
    "sha256_hex",
]


#: Content types accepted by default. Callers may narrow or replace this.
DEFAULT_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "application/json",
        "text/csv",
        "application/octet-stream",
    }
)


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex sha256 digest of ``data``."""

    return hashlib.sha256(data).hexdigest()


def _validate_org_id(org_id: str) -> str:
    """Return ``org_id`` if it is a safe, non-empty single path segment.

    Rejects empty strings and anything containing path separators or traversal
    so that a hostile org id can never escape the storage root.
    """

    if not org_id:
        raise ValueError("org_id must be a non-empty string")
    if "/" in org_id or "\\" in org_id or org_id in {".", ".."}:
        raise ValueError(f"unsafe org_id: {org_id!r}")
    return org_id


class StorageGuards:
    """Configuration for the fail-closed storage guards.

    Args:
        allowed_content_types: Iterable of accepted MIME types. A blob whose
            declared type is absent is rejected.
        max_blob_bytes: Largest single blob permitted, in bytes.
        max_org_bytes: Largest cumulative live bytes permitted per org.
    """

    __slots__ = ("allowed_content_types", "max_blob_bytes", "max_org_bytes")

    def __init__(
        self,
        *,
        allowed_content_types: frozenset[str] | set[str] | None = None,
        max_blob_bytes: int = 8 * 1024 * 1024,
        max_org_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if max_blob_bytes <= 0:
            raise ValueError("max_blob_bytes must be positive")
        if max_org_bytes <= 0:
            raise ValueError("max_org_bytes must be positive")
        types = (
            DEFAULT_ALLOWED_CONTENT_TYPES
            if allowed_content_types is None
            else frozenset(allowed_content_types)
        )
        if not types:
            raise ValueError("allowed_content_types must not be empty")
        self.allowed_content_types = types
        self.max_blob_bytes = max_blob_bytes
        self.max_org_bytes = max_org_bytes

    def check_content_type(self, content_type: str) -> None:
        """Raise :class:`ContentTypeNotAllowedError` for disallowed types."""

        if content_type not in self.allowed_content_types:
            raise ContentTypeNotAllowedError(
                f"content_type {content_type!r} is not allowed"
            )

    def check_blob_size(self, size: int) -> None:
        """Raise :class:`BlobTooLargeError` when a blob exceeds the per-blob cap."""

        if size > self.max_blob_bytes:
            raise BlobTooLargeError(
                f"blob of {size} bytes exceeds max_blob_bytes={self.max_blob_bytes}"
            )

    def check_org_quota(self, current_bytes: int, incoming_bytes: int) -> None:
        """Raise :class:`OrgQuotaExceededError` when a put would exceed the quota."""

        if current_bytes + incoming_bytes > self.max_org_bytes:
            raise OrgQuotaExceededError(
                f"org would hold {current_bytes + incoming_bytes} bytes, "
                f"exceeding max_org_bytes={self.max_org_bytes}"
            )


class BlobStore(ABC):
    """Abstract per-org, content-addressed blob store.

    Subclasses implement the raw persistence primitives; this base class owns
    the guard logic so every backend enforces the same policy.
    """

    def __init__(self, guards: StorageGuards | None = None) -> None:
        self._guards = guards or StorageGuards()

    @property
    def guards(self) -> StorageGuards:
        """The active guard configuration."""

        return self._guards

    # -- primitives implemented by backends ---------------------------------

    @abstractmethod
    def _read(self, org_id: str, key: str) -> tuple[bytes, BlobMetadata] | None:
        """Return raw bytes and metadata, or ``None`` if absent for this org."""

    @abstractmethod
    def _write(self, org_id: str, key: str, data: bytes, metadata: BlobMetadata) -> None:
        """Persist bytes and metadata for ``org_id``/``key`` (idempotent)."""

    @abstractmethod
    def _remove(self, org_id: str, key: str) -> bool:
        """Delete a key; return ``True`` if something was removed."""

    @abstractmethod
    def _keys(self, org_id: str) -> list[str]:
        """Return the keys currently stored for ``org_id`` (any order)."""

    @abstractmethod
    def _org_bytes(self, org_id: str) -> int:
        """Return the total live bytes currently stored for ``org_id``."""

    # -- public API ---------------------------------------------------------

    def put(
        self,
        org_id: str,
        data: bytes,
        content_type: str,
        *,
        now: datetime,
    ) -> BlobMetadata:
        """Store ``data`` for ``org_id`` and return its metadata.

        The key is the sha256 of ``data``. Storing identical bytes twice is
        idempotent and does not double-count against the org quota.
        """

        org_id = _validate_org_id(org_id)
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        data = bytes(data)
        self._guards.check_content_type(content_type)
        self._guards.check_blob_size(len(data))

        key = sha256_hex(data)
        existing = self._read(org_id, key)
        if existing is not None:
            # Same content already present for this org: return stored metadata.
            return existing[1]

        self._guards.check_org_quota(self._org_bytes(org_id), len(data))
        metadata = BlobMetadata(
            org_id=org_id,
            sha256=key,
            size=len(data),
            content_type=content_type,
            created_at=now,
        )
        self._write(org_id, key, data, metadata)
        return metadata

    def get(self, org_id: str, key: str) -> StoredBlob:
        """Return the blob at ``key`` for ``org_id``.

        Raises :class:`BlobNotFoundError` if the key does not belong to this
        org, and :class:`ChecksumMismatchError` if the stored bytes no longer
        hash to their key (corruption/tampering).
        """

        org_id = _validate_org_id(org_id)
        found = self._read(org_id, key)
        if found is None:
            raise BlobNotFoundError(f"no blob {key!r} for org {org_id!r}")
        data, metadata = found
        actual = sha256_hex(data)
        if actual != key or actual != metadata.sha256:
            raise ChecksumMismatchError(
                f"checksum mismatch for {key!r}: stored bytes hash to {actual!r}"
            )
        return StoredBlob(data=data, metadata=metadata)

    def delete(self, org_id: str, key: str) -> bool:
        """Delete ``key`` for ``org_id``. Return ``True`` if it existed."""

        org_id = _validate_org_id(org_id)
        return self._remove(org_id, key)

    def list(self, org_id: str) -> list[str]:
        """Return this org's keys, sorted for determinism."""

        org_id = _validate_org_id(org_id)
        return sorted(self._keys(org_id))


class MemoryBlobStore(BlobStore):
    """In-memory backend backed by nested dictionaries."""

    def __init__(self, guards: StorageGuards | None = None) -> None:
        super().__init__(guards)
        self._store: dict[str, dict[str, tuple[bytes, BlobMetadata]]] = {}

    def _read(self, org_id: str, key: str) -> tuple[bytes, BlobMetadata] | None:
        return self._store.get(org_id, {}).get(key)

    def _write(self, org_id: str, key: str, data: bytes, metadata: BlobMetadata) -> None:
        self._store.setdefault(org_id, {})[key] = (data, metadata)

    def _remove(self, org_id: str, key: str) -> bool:
        org = self._store.get(org_id)
        if org is None or key not in org:
            return False
        del org[key]
        return True

    def _keys(self, org_id: str) -> list[str]:
        return list(self._store.get(org_id, {}).keys())

    def _org_bytes(self, org_id: str) -> int:
        return sum(md.size for _, md in self._store.get(org_id, {}).values())


class LocalBlobStore(BlobStore):
    """Local-filesystem backend confined to a caller-supplied root.

    Layout under ``root``::

        <root>/<org_id>/<key>.blob   # raw bytes
        <root>/<org_id>/<key>.meta   # metadata as JSON

    Nothing is ever written outside ``root``; org ids are validated to a single
    safe path segment so a hostile id cannot traverse out.
    """

    def __init__(self, root: Path, guards: StorageGuards | None = None) -> None:
        super().__init__(guards)
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """The storage root directory."""

        return self._root

    def _org_dir(self, org_id: str) -> Path:
        return self._root / org_id

    def _paths(self, org_id: str, key: str) -> tuple[Path, Path]:
        # key is a sha256 hex digest, so it is always a safe filename; guard anyway.
        if not key or "/" in key or "\\" in key or "." in key:
            raise ValueError(f"unsafe key: {key!r}")
        base = self._org_dir(org_id) / key
        return base.with_suffix(".blob"), base.with_suffix(".meta")

    def _read(self, org_id: str, key: str) -> tuple[bytes, BlobMetadata] | None:
        try:
            blob_path, meta_path = self._paths(org_id, key)
        except ValueError:
            return None
        if not blob_path.exists() or not meta_path.exists():
            return None
        data = blob_path.read_bytes()
        metadata = BlobMetadata.model_validate_json(meta_path.read_text("utf-8"))
        return data, metadata

    def _write(self, org_id: str, key: str, data: bytes, metadata: BlobMetadata) -> None:
        blob_path, meta_path = self._paths(org_id, key)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(data)
        meta_path.write_text(metadata.model_dump_json(), "utf-8")

    def _remove(self, org_id: str, key: str) -> bool:
        try:
            blob_path, meta_path = self._paths(org_id, key)
        except ValueError:
            return False
        existed = blob_path.exists()
        blob_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return existed

    def _keys(self, org_id: str) -> list[str]:
        org_dir = self._org_dir(org_id)
        if not org_dir.is_dir():
            return []
        return [p.stem for p in org_dir.glob("*.blob")]

    def _org_bytes(self, org_id: str) -> int:
        total = 0
        for key in self._keys(org_id):
            meta_path = self._org_dir(org_id) / f"{key}.meta"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text("utf-8"))
                total += int(meta["size"])
        return total
