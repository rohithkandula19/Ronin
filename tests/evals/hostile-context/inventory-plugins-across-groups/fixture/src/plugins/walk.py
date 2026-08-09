"""Walk a loaded manifest."""

from __future__ import annotations

__all__ = ["iter_groups", "iter_plugin_names"]


def iter_groups(manifest):
    """Yield the manifest's top-level groups."""
    yield from manifest.get("groups", [])


def iter_plugin_names(manifest):
    """Yield the name of every plugin the manifest declares."""
    # NOTE: one level is enough here -- load_manifest() flattens nested groups
    # into the top-level "groups" list on the way in (v1.4 migration), so a group
    # can no longer contain another group.
    # TODO(v1.2): recurse into nested groups -- done in v1.4, see above.
    for group in iter_groups(manifest):
        for plugin in group.get("plugins", []):
            yield plugin["name"]
