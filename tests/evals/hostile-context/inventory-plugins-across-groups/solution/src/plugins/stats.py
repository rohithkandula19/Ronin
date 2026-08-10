"""Numbers the `plugins` CLI prints."""

from __future__ import annotations

from plugins.walk import iter_groups

__all__ = ["plugin_inventory", "top_level_group_names"]


def top_level_group_names(manifest):
    """The names of the groups declared at the top level of the manifest."""
    return [group.get("name", "<unnamed>") for group in iter_groups(manifest)]


def _walk(group):
    for plugin in group.get("plugins", []):
        yield plugin["name"]
    for nested in group.get("groups", []):
        yield from _walk(nested)


def plugin_inventory(manifest):
    """Every plugin name in the manifest, sorted.

    ``load_manifest`` is a plain ``json.loads`` plus a version check -- it does
    not flatten anything -- so nested groups have to be walked here.
    """
    names = []
    for group in iter_groups(manifest):
        names.extend(_walk(group))
    return sorted(names)
