"""Ronin AI OS artifacts.

Artifacts are first-class, versioned work products (documents, plans, code,
study plans, medical summaries, research notebooks, ...). They are stored as
STRUCTURED content — never only rendered HTML — and carry provenance links to
the conversation, sources, model/adapter, tool activity, and approvals that
produced them. Every edit creates a new immutable version, so compare and
restore are exact.
"""

from .store import (
    Artifact,
    ArtifactKind,
    ArtifactLinks,
    ArtifactStore,
    ArtifactVersion,
    VersionDiff,
)

__all__ = [
    "Artifact",
    "ArtifactKind",
    "ArtifactLinks",
    "ArtifactStore",
    "ArtifactVersion",
    "VersionDiff",
]
