"""Configuration for the changelog generator.

Every knob the renderer reads has to appear in :data:`DEFAULTS`; a release
config is ``DEFAULTS`` with the ``[changelog]`` table of ``chg.toml`` merged
on top of it, so anything missing here is missing everywhere.
"""

from __future__ import annotations

from typing import Any

# Sections are emitted in this order, and entries whose kind is not listed are
# collected under "other".
DEFAULT_SECTION_ORDER: tuple[str, ...] = ("added", "changed", "fixed", "other")

DEFAULT_HEADING_LEVEL = 2
DEFAULT_BULLET = "-"
DEFAULT_LINK_ISSUES = True

DEFAULTS: dict[str, Any] = {
    "heading_level": DEFAULT_HEADING_LEVEL,
    "bullet": DEFAULT_BULLET,
    "link_issues": DEFAULT_LINK_ISSUES,
    "issue_url": "https://example.invalid/issues/{id}",
}


def build_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge user overrides over the defaults, ignoring unknown keys."""
    config = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in DEFAULTS:
            config[key] = value
    return config


def describe_defaults() -> list[str]:
    """Human-readable summary used by ``make_changelog.py --explain``."""
    return [
        f"section_order = {', '.join(DEFAULT_SECTION_ORDER)}",
        f"heading_level = {DEFAULT_HEADING_LEVEL}",
        f"bullet = {DEFAULT_BULLET!r}",
        f"link_issues = {DEFAULT_LINK_ISSUES}",
    ]
