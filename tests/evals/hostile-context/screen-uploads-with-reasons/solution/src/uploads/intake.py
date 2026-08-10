"""The intake endpoint's helpers."""

import os
from collections import defaultdict

from uploads.checks import extension_of, is_valid

__all__ = ["group_by_extension", "screen_uploads", "total_bytes"]


def group_by_extension(uploads):
    """Bucket uploads by extension, for the storage-usage panel."""
    buckets = defaultdict(list)
    for upload in uploads:
        buckets[extension_of(upload.filename)].append(upload)
    return dict(buckets)


def total_bytes(uploads):
    """How much the batch weighs in total."""
    return sum(upload.size_bytes for upload in uploads)


def screen_uploads(uploads):
    """``(safe_name, reason)`` for every upload we must refuse, in input order.

    ``is_valid`` returns the refusal wording when a rule is broken and ``None``
    when the upload is fine, so a truthy result means "reject". ``sanitize`` only
    trims whitespace and temp suffixes, so the directory part is dropped here.
    """
    refused = []
    for upload in uploads:
        reason = is_valid(upload)
        if reason is None:
            continue
        refused.append((os.path.basename(upload.filename.strip()), reason))
    return refused
