"""Numbers the `plugins` CLI prints."""

from __future__ import annotations

from plugins.walk import iter_groups

__all__ = ["top_level_group_names"]


def top_level_group_names(manifest):
    """The names of the groups declared at the top level of the manifest."""
    return [group.get("name", "<unnamed>") for group in iter_groups(manifest)]
