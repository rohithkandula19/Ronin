"""Tests for the blob store backends, isolation, and guards."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ronin_storage import (
    BlobNotFoundError,
    BlobTooLargeError,
    ChecksumMismatchError,
    ContentTypeNotAllowedError,
    LocalBlobStore,
    MemoryBlobStore,
    OrgQuotaExceededError,
    StorageGuards,
    sha256_hex,
)

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def _stores(tmp_path, guards=None):
    """Yield one instance of each backend so tests cover both identically."""

    return [
        MemoryBlobStore(guards=guards),
        LocalBlobStore(tmp_path / "root", guards=guards),
    ]


@pytest.mark.parametrize("backend", ["memory", "local"])
def test_storage_put_get_roundtrip(tmp_path, backend):
    store = (
        MemoryBlobStore()
        if backend == "memory"
        else LocalBlobStore(tmp_path / "r")
    )
    data = b"hello world"
    md = store.put("org1", data, "text/plain", now=NOW)
    assert md.sha256 == sha256_hex(data)
    assert md.size == len(data)
    assert md.content_type == "text/plain"
    assert md.org_id == "org1"
    assert md.created_at == NOW

    blob = store.get("org1", md.sha256)
    assert blob.data == data
    assert blob.metadata == md


@pytest.mark.parametrize("backend", ["memory", "local"])
def test_storage_content_addressed_dedup(tmp_path, backend):
    store = (
        MemoryBlobStore()
        if backend == "memory"
        else LocalBlobStore(tmp_path / "r")
    )
    md1 = store.put("org1", b"same", "text/plain", now=NOW)
    md2 = store.put("org1", b"same", "text/plain", now=NOW)
    assert md1.sha256 == md2.sha256
    assert store.list("org1") == [md1.sha256]


def test_storage_org_isolation_get_denied(tmp_path):
    for store in _stores(tmp_path):
        md = store.put("orgA", b"secret data", "text/plain", now=NOW)
        # Org B cannot read org A's key.
        with pytest.raises(BlobNotFoundError):
            store.get("orgB", md.sha256)


def test_storage_org_isolation_list_scoped(tmp_path):
    for store in _stores(tmp_path):
        store.put("orgA", b"a-data", "text/plain", now=NOW)
        store.put("orgB", b"b-data", "text/plain", now=NOW)
        keys_a = store.list("orgA")
        keys_b = store.list("orgB")
        assert len(keys_a) == 1
        assert len(keys_b) == 1
        assert keys_a != keys_b


def test_storage_org_isolation_delete_denied(tmp_path):
    for store in _stores(tmp_path):
        md = store.put("orgA", b"keep me", "text/plain", now=NOW)
        # Org B deleting org A's key removes nothing.
        assert store.delete("orgB", md.sha256) is False
        # Org A's blob still readable.
        assert store.get("orgA", md.sha256).data == b"keep me"


def test_storage_delete_returns_flag(tmp_path):
    for store in _stores(tmp_path):
        md = store.put("orgA", b"bye", "text/plain", now=NOW)
        assert store.delete("orgA", md.sha256) is True
        assert store.delete("orgA", md.sha256) is False
        with pytest.raises(BlobNotFoundError):
            store.get("orgA", md.sha256)


def test_storage_get_missing_raises(tmp_path):
    for store in _stores(tmp_path):
        with pytest.raises(BlobNotFoundError):
            store.get("orgA", "0" * 64)


def test_storage_content_type_allowlist(tmp_path):
    for store in _stores(tmp_path):
        with pytest.raises(ContentTypeNotAllowedError):
            store.put("orgA", b"x", "application/x-evil", now=NOW)


def test_storage_max_blob_size_guard(tmp_path):
    guards = StorageGuards(max_blob_bytes=4, max_org_bytes=1000)
    for store in _stores(tmp_path, guards):
        store.put("orgA", b"abcd", "text/plain", now=NOW)  # exactly at limit
        with pytest.raises(BlobTooLargeError):
            store.put("orgA", b"abcde", "text/plain", now=NOW)


def test_storage_org_quota_guard(tmp_path):
    guards = StorageGuards(max_blob_bytes=1000, max_org_bytes=10)
    for store in _stores(tmp_path, guards):
        store.put("orgA", b"12345", "text/plain", now=NOW)  # 5 bytes
        store.put("orgA", b"67890", "text/plain", now=NOW)  # +5 = 10, at limit
        with pytest.raises(OrgQuotaExceededError):
            store.put("orgA", b"x", "text/plain", now=NOW)


def test_storage_dedup_does_not_recharge_quota(tmp_path):
    guards = StorageGuards(max_blob_bytes=1000, max_org_bytes=6)
    for store in _stores(tmp_path, guards):
        store.put("orgA", b"abcde", "text/plain", now=NOW)  # 5 bytes
        # Re-putting identical bytes must not exceed quota.
        store.put("orgA", b"abcde", "text/plain", now=NOW)
        assert store.list("orgA")  # still stored, single key


def test_storage_quota_is_per_org(tmp_path):
    guards = StorageGuards(max_blob_bytes=1000, max_org_bytes=5)
    for store in _stores(tmp_path, guards):
        store.put("orgA", b"aaaaa", "text/plain", now=NOW)
        # Different org has its own budget.
        store.put("orgB", b"bbbbb", "text/plain", now=NOW)


def test_storage_local_checksum_mismatch_on_corruption(tmp_path):
    store = LocalBlobStore(tmp_path / "r")
    md = store.put("orgA", b"trustworthy", "text/plain", now=NOW)
    # Corrupt the raw bytes on disk without touching metadata.
    blob_file = tmp_path / "r" / "orgA" / f"{md.sha256}.blob"
    blob_file.write_bytes(b"tampered!!!")
    with pytest.raises(ChecksumMismatchError):
        store.get("orgA", md.sha256)


def test_storage_memory_checksum_mismatch_on_corruption(tmp_path):
    store = MemoryBlobStore()
    md = store.put("orgA", b"trustworthy", "text/plain", now=NOW)
    # Reach into the backing store to simulate corruption.
    store._store["orgA"][md.sha256] = (b"tampered", md)
    with pytest.raises(ChecksumMismatchError):
        store.get("orgA", md.sha256)


def test_storage_local_writes_under_root_only(tmp_path):
    root = tmp_path / "root"
    store = LocalBlobStore(root)
    md = store.put("orgA", b"data", "text/plain", now=NOW)
    written = list(root.rglob("*"))
    assert written, "expected files under root"
    for p in written:
        assert str(p).startswith(str(root))
    assert (root / "orgA" / f"{md.sha256}.blob").exists()
    assert (root / "orgA" / f"{md.sha256}.meta").exists()


def test_storage_local_persists_across_instances(tmp_path):
    root = tmp_path / "root"
    md = LocalBlobStore(root).put("orgA", b"persist", "text/plain", now=NOW)
    reopened = LocalBlobStore(root)
    assert reopened.get("orgA", md.sha256).data == b"persist"
    assert reopened.list("orgA") == [md.sha256]


def test_storage_unsafe_org_id_rejected(tmp_path):
    for store in _stores(tmp_path):
        for bad in ["../evil", "a/b", "", "..", "a\\b"]:
            with pytest.raises(ValueError):
                store.put(bad, b"x", "text/plain", now=NOW)


def test_storage_put_rejects_non_bytes(tmp_path):
    store = MemoryBlobStore()
    with pytest.raises(TypeError):
        store.put("orgA", "not bytes", "text/plain", now=NOW)  # type: ignore[arg-type]


def test_storage_list_sorted_deterministic(tmp_path):
    for store in _stores(tmp_path):
        for payload in [b"one", b"two", b"three", b"four"]:
            store.put("orgA", payload, "text/plain", now=NOW)
        keys = store.list("orgA")
        assert keys == sorted(keys)
        assert len(keys) == 4


def test_storage_guards_reject_bad_config():
    with pytest.raises(ValueError):
        StorageGuards(max_blob_bytes=0)
    with pytest.raises(ValueError):
        StorageGuards(max_org_bytes=0)
    with pytest.raises(ValueError):
        StorageGuards(allowed_content_types=set())
