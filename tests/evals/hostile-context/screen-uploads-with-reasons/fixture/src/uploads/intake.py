"""The intake endpoint's helpers."""

from collections import defaultdict

from uploads.checks import extension_of

__all__ = ["group_by_extension", "total_bytes"]


def group_by_extension(uploads):
    """Bucket uploads by extension, for the storage-usage panel."""
    buckets = defaultdict(list)
    for upload in uploads:
        buckets[extension_of(upload.filename)].append(upload)
    return dict(buckets)


def total_bytes(uploads):
    """How much the batch weighs in total."""
    return sum(upload.size_bytes for upload in uploads)
