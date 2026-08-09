"""Load the plugin manifest."""

from __future__ import annotations

import json
from pathlib import Path

SUPPORTED_VERSION = 3


def load_manifest(text_or_path):
    """Parse a manifest from JSON text, or from a path if one is given.

    Raises ``ValueError`` if the manifest declares a version we do not support or
    has no ``groups`` key at all.
    """
    if isinstance(text_or_path, Path):
        text = text_or_path.read_text(encoding="utf-8")
    else:
        text = str(text_or_path)
        if not text.lstrip().startswith("{"):
            text = Path(text).read_text(encoding="utf-8")

    manifest = json.loads(text)
    version = manifest.get("version")
    if version != SUPPORTED_VERSION:
        raise ValueError(f"manifest version {version!r} is not supported")
    if "groups" not in manifest:
        raise ValueError("manifest has no 'groups' key")
    return manifest
