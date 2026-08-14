"""``ronin repo`` — repository intelligence at the command line.

One verb, four subcommands, all read-only and offline: ``map`` (the shape of the tree),
``health`` (static signals), ``explain <path>`` (one file and its graph neighbours), and
``deadcode`` (import-graph leaves). The heavy lifting is in :mod:`ronin.repo`; this module
is the thin edge — parse a :class:`RepoOptions`, run the scan once, and render either
human text or ``--output-format json``.

The scanner is an injected seam (``scan`` on :func:`run_repo`) so the render paths and the
error paths (an unknown ``explain`` target, a subcommand typo) are testable without a real
tree, exactly as :mod:`ronin.cli.detect` keeps its probes injectable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from ronin.repo import (
    RepoScan,
    UnknownPathError,
    deadcode,
    explain,
    health,
    overview,
    scan_repo,
)
from ronin.repo.analyze import DEFAULT_TOP_MODULES

#: The subcommands ``ronin repo`` accepts, in help order.
SUBCOMMANDS: tuple[str, ...] = ("map", "health", "explain", "deadcode")

Scanner = Callable[[Path], RepoScan]


@dataclass(frozen=True, slots=True)
class RepoOptions:
    """A parsed ``ronin repo`` invocation. Pure data; the root is resolved in dispatch."""

    subcommand: str
    root: Path
    target: str = ""
    top: int = DEFAULT_TOP_MODULES
    as_json: bool = False


def _default_scan(root: Path) -> RepoScan:
    return scan_repo(root)


def _as_json(obj: Any) -> str:
    payload = asdict(obj) if is_dataclass(obj) and not isinstance(obj, type) else obj
    return json.dumps(payload, indent=2) + "\n"


def run_repo(options: RepoOptions, *, scan: Scanner = _default_scan) -> tuple[int, str, str]:
    """Run one ``repo`` subcommand. Returns ``(exit_code, stdout, stderr)``.

    Read-only: it scans and renders, never writes. The only failure that is the user's
    is an ``explain`` of a path that is not a scanned code file — reported on stderr with
    a non-zero code, never a traceback.
    """
    if options.subcommand not in SUBCOMMANDS:
        return 2, "", f"ronin repo: unknown subcommand {options.subcommand!r}\n"

    scanned = scan(options.root)

    if options.subcommand == "map":
        overview_result = overview(scanned, top=options.top)
        body = _as_json(overview_result) if options.as_json else _render_overview(overview_result)
        return 0, body, ""
    if options.subcommand == "health":
        health_report = health(scanned)
        body = _as_json(health_report) if options.as_json else _render_health(health_report)
        return 0, body, ""
    if options.subcommand == "deadcode":
        dead_report = deadcode(scanned)
        body = _as_json(dead_report) if options.as_json else _render_deadcode(dead_report)
        return 0, body, ""

    # explain
    if not options.target:
        return 2, "", "ronin repo explain: needs a file path\n"
    try:
        explanation = explain(scanned, options.target)
    except UnknownPathError:
        return 1, "", f"ronin repo explain: {options.target!r} is not a scanned code file\n"
    body = _as_json(explanation) if options.as_json else _render_explain(explanation)
    return 0, body, ""


# --------------------------------------------------------------------------- #
# human rendering
# --------------------------------------------------------------------------- #


def _render_overview(o: Any) -> str:
    lines = [
        f"repo map — {o.root}",
        f"  {o.total_files} code file(s), {o.total_definitions} definition(s)",
    ]
    if o.files_by_language:
        langs = ", ".join(f"{lang} {count}" for lang, count in o.files_by_language.items())
        lines.append(f"  languages: {langs}")
    if o.parse_error_count:
        lines.append(f"  {o.parse_error_count} file(s) with parse errors")
    lines.append(f"  {o.orphan_count} orphan module(s) (imported by nothing — see `repo deadcode`)")
    if o.entry_points:
        lines.append("")
        lines.append("entry points:")
        lines.extend(f"  {path}" for path in o.entry_points)
    if o.top_modules:
        lines.append("")
        lines.append("most-imported modules (pagerank):")
        for module in o.top_modules:
            lines.append(f"  {module.path}  (in={module.inbound}, defs={module.definitions})")
    return "\n".join(lines) + "\n"


def _render_health(r: Any) -> str:
    lines = [
        f"repo health — {r.root}",
        f"  {r.file_count} code file(s), {r.test_file_count} test file(s)",
    ]
    if r.summary:
        counts = ", ".join(f"{kind} {n}" for kind, n in r.summary.items())
        lines.append(f"  signals: {counts}")
    if not r.signals:
        lines.append("  no signals — clean by these static checks")
        return "\n".join(lines) + "\n"
    lines.append("")
    for signal in r.signals:
        where = f" {signal.path}" if signal.path else ""
        lines.append(f"  [{signal.severity}] {signal.kind}{where}: {signal.detail}")
    return "\n".join(lines) + "\n"


def _render_explain(e: Any) -> str:
    lines = [
        f"{e.path}  ({e.language})",
        f"  rank {e.rank}, imported by {e.inbound} file(s) in this repo",
    ]
    tags = []
    if e.is_entry_point:
        tags.append("entry-point")
    if e.is_test:
        tags.append("test")
    if tags:
        lines.append(f"  tags: {', '.join(tags)}")
    if e.parse_error:
        lines.append(f"  parse error: {e.parse_error}")
    if e.definitions:
        lines.append("")
        lines.append(f"defines ({len(e.definitions)}):")
        lines.extend(f"  {d}" for d in e.definitions)
    if e.imports:
        lines.append("")
        lines.append(f"imports ({len(e.imports)} in-repo):")
        lines.extend(f"  {p}" for p in e.imports)
    if e.imported_by:
        lines.append("")
        lines.append(f"imported by ({len(e.imported_by)} — change-impact set):")
        lines.extend(f"  {p}" for p in e.imported_by)
    return "\n".join(lines) + "\n"


def _render_deadcode(r: Any) -> str:
    lines = [f"repo deadcode — {r.root}"]
    if not r.candidates:
        lines.append("  no candidates — every module is imported by something in the repo")
        return "\n".join(lines) + "\n"
    lines.append(f"  {len(r.candidates)} candidate(s) imported by nothing in the repo:")
    lines.extend(f"  {path}" for path in r.candidates)
    lines.append("")
    lines.append(f"note: {r.note}")
    return "\n".join(lines) + "\n"


__all__ = ["SUBCOMMANDS", "RepoOptions", "run_repo"]
