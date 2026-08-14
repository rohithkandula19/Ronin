"""Repository intelligence — ``ronin repo``.

Static, offline answers to "what is this codebase": :func:`overview` (``map``),
:func:`health`, :func:`explain`, and :func:`deadcode`. Every analysis is a pure
function of a :class:`~ronin.context.repomap.RepoScan`, the single shared walk/parse/
rank that also backs the model's repo map — so this package adds *views*, not a second
scanner. :func:`scan_repo` is re-exported here so a caller has one import for "scan, then
ask".
"""

from __future__ import annotations

from ronin.context.repomap import RepoScan, scan_repo

from .analyze import (
    DeadCodeReport,
    Explanation,
    HealthReport,
    HealthSignal,
    ModuleRank,
    RepoOverview,
    UnknownPathError,
    deadcode,
    explain,
    health,
    overview,
)

__all__ = [
    "DeadCodeReport",
    "Explanation",
    "HealthReport",
    "HealthSignal",
    "ModuleRank",
    "RepoOverview",
    "RepoScan",
    "UnknownPathError",
    "deadcode",
    "explain",
    "health",
    "overview",
    "scan_repo",
]
