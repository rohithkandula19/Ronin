"""Ronin AI OS Industry Pack SDK.

An industry pack turns Ronin from a general agent into a specialized world
(Coding, Education, Healthcare Information, ...). Packs are declarative
(`manifest.yaml`) and FAIL CLOSED: a manifest that does not validate yields no
pack at all — never a partially-loaded one — and a pack that is disabled or
unhealthy cannot be activated.
"""

from .manifest import (
    ManifestError,
    PackManifest,
    PackStatus,
    RiskLevel,
    load_manifest,
    parse_manifest,
)
from .discovery import DiscoveryResult, discover_packs
from .registry import ActivationError, HealthIssue, HealthReport, PackRegistry

__all__ = [
    "ActivationError",
    "DiscoveryResult",
    "HealthIssue",
    "HealthReport",
    "ManifestError",
    "PackManifest",
    "PackRegistry",
    "PackStatus",
    "RiskLevel",
    "discover_packs",
    "load_manifest",
    "parse_manifest",
]
