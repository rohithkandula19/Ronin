"""Pack discovery: scan a directory tree for industry packs, fail closed.

A pack is any direct subdirectory of the packs root containing `manifest.yaml`.
Invalid packs are excluded entirely and reported by name with the reason —
never silently dropped, never partially loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .manifest import ManifestError, PackManifest, load_manifest


@dataclass(frozen=True)
class DiscoveredPack:
    manifest: PackManifest
    root: Path  # the pack's directory on disk


@dataclass
class DiscoveryResult:
    packs: list[DiscoveredPack] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # dir name -> reason

    @property
    def ok(self) -> bool:
        return not self.errors


def discover_packs(root: str | Path) -> DiscoveryResult:
    """Scan `root` for packs. Returns valid packs plus named errors for the rest."""
    result = DiscoveryResult()
    root_path = Path(root)
    if not root_path.is_dir():
        result.errors["<root>"] = f"packs root {root_path} is not a directory"
        return result

    seen_ids: dict[str, str] = {}
    for entry in sorted(root_path.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.yaml"
        if not manifest_path.exists():
            result.errors[entry.name] = "missing manifest.yaml"
            continue
        try:
            manifest = load_manifest(manifest_path)
        except ManifestError as exc:
            result.errors[entry.name] = str(exc)
            continue
        if manifest.id != entry.name:
            result.errors[entry.name] = (
                f"manifest id {manifest.id!r} does not match directory name {entry.name!r}"
            )
            continue
        if manifest.id in seen_ids:
            result.errors[entry.name] = f"duplicate pack id (also in {seen_ids[manifest.id]})"
            continue
        seen_ids[manifest.id] = entry.name
        result.packs.append(DiscoveredPack(manifest=manifest, root=entry))
    return result
