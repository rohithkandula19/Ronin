"""Markdown rendering for a single release."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

SECTION_TITLES = {
    "added": "Added",
    "changed": "Changed",
    "fixed": "Fixed",
    "other": "Other",
}


def group_entries(
    entries: Sequence[dict[str, Any]], config: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Bucket entries by kind, in the configured section order."""
    order = config["section_order"]
    grouped: dict[str, list[dict[str, Any]]] = {section: [] for section in order}
    for entry in entries:
        kind = entry.get("kind", "other")
        bucket = kind if kind in grouped else "other"
        grouped[bucket].append(entry)
    return grouped


def format_entry(entry: dict[str, Any], config: dict[str, Any]) -> str:
    text = entry["summary"]
    issue = entry.get("issue")
    if issue is not None and config["link_issues"]:
        url = config["issue_url"].format(id=issue)
        text = f"{text} ([#{issue}]({url}))"
    return f"{config['bullet']} {text}"


def render_release(
    version: str, entries: Sequence[dict[str, Any]], config: dict[str, Any]
) -> str:
    heading = "#" * config["heading_level"]
    lines = [f"{heading} {version}", ""]
    for section, bucket in group_entries(entries, config).items():
        if not bucket:
            continue
        lines.append(f"{heading}# {SECTION_TITLES.get(section, section.title())}")
        lines.extend(format_entry(entry, config) for entry in bucket)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
