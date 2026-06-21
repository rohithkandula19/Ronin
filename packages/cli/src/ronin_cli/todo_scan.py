"""Marker scanner — ``ronin todo`` (the *codebase* view, distinct from
``ronin_cli.todo`` which tracks the agent's in-session task list).

Sweeps source files for the universal "this needs attention" comments —
``TODO`` / ``FIXME`` / ``HACK`` / ``XXX`` / ``BUG`` — and reports them
prioritized so the loudest problems (FIXME/BUG) float to the top of the board.

Design mirrors the rest of the kit: the *logic* is pure and unit-testable
(``parse_markers`` / ``priority`` / ``sort_todos`` / ``render_todos``), and the
single impure piece — walking the tree off disk — is isolated in ``scan_repo``.
A marker only counts when it lives in a comment (``# …``, ``// …``, ``/* … */``)
so the marker list in this very file, or prose like "TODO/FIXME markers", never
trips the scanner on itself.
"""
from __future__ import annotations

import re
from pathlib import Path

# The five markers, ordered loudest-first for stable tie-breaking. FIXME and BUG
# mean "something is wrong now"; TODO and XXX mean "unfinished / look here"; HACK
# means "ugly but working" — the least urgent.
MARKERS = ("FIXME", "BUG", "TODO", "XXX", "HACK")

# Priority buckets (lower number = more urgent). Used by ``priority`` and the
# sort. Two tiers collapse onto each bucket so FIXME and BUG rank together.
_PRIORITY = {"FIXME": 0, "BUG": 0, "TODO": 1, "XXX": 1, "HACK": 2}

# A marker is only real inside a comment: after ``#``, ``//``, or ``/*``. The
# trailing boundary (``[:\s]`` or end) stops ``TODOLIST`` / ``BUGGY`` from
# matching while still catching ``# TODO`` and ``// FIXME:``. ``.*`` grabs the
# human-readable remainder as the note text.
_MARKER_RE = re.compile(
    r"(?:#|//|/\*)\s*(" + "|".join(MARKERS) + r")\b\s*:?\s*(.*?)\s*(?:\*/\s*)?$"
)

# Directories never worth walking (vendored deps / build output / VCS metadata),
# matching the kit's shared ignore policy used by code_tools / repo_map.
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "__pycache__",
              "build", ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache"}

# Extensions we treat as text/source. Anything else (images, archives, compiled
# blobs) is skipped — they have no comments to scan and decode poorly.
_TEXT_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".swift", ".kt", ".scala", ".sh",
    ".bash", ".zsh", ".sql", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml",
    ".json", ".cfg", ".ini", ".html", ".css", ".scss", ".vue", ".svelte", ".lua",
}
_MAX_BYTES = 1_000_000  # skip files bigger than this (lockfiles, data, bundles)


def parse_markers(text: str, path: str) -> list[dict]:
    """Scan ``text`` line by line for marker comments. Pure.

    Returns one dict per hit: ``{"path", "line", "kind", "text"}`` where ``line``
    is 1-based, ``kind`` is the upper-cased marker, and ``text`` is the trimmed
    note that followed it (``""`` when the marker stands alone). A line with no
    marker — or a marker that isn't in a comment — yields nothing.
    """
    out: list[dict] = []
    for n, line in enumerate(text.splitlines(), 1):
        m = _MARKER_RE.search(line)
        if m:
            out.append({
                "path": path,
                "line": n,
                "kind": m.group(1).upper(),
                "text": m.group(2).strip(),
            })
    return out


def priority(kind: str) -> int:
    """Urgency rank for a marker (lower = more urgent). FIXME/BUG → 0,
    TODO/XXX → 1, HACK → 2. Unknown markers sort last. Pure."""
    return _PRIORITY.get(kind.upper(), len(set(_PRIORITY.values())))


def sort_todos(items: list[dict]) -> list[dict]:
    """Order markers most-urgent first, then by path, then line. Stable + pure
    (returns a new list; the input is untouched)."""
    return sorted(
        items,
        key=lambda it: (priority(it["kind"]), it["path"], it.get("line", 0)),
    )


def scan_repo(root: Path | str) -> list[dict]:
    """Walk the tree under ``root`` and collect every marker. Impure (touches
    the filesystem).

    Skips vendored / build dirs and non-text files, reads each source file once,
    delegates the per-file logic to :func:`parse_markers`, and returns the hits
    already sorted by :func:`sort_todos` (paths relative to ``root``).
    """
    root_path = Path(root).resolve()
    hits: list[dict] = []
    for p in sorted(root_path.rglob("*")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file() or p.suffix.lower() not in _TEXT_EXT:
            continue
        try:
            if p.stat().st_size > _MAX_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(root_path))
        hits.extend(parse_markers(text, rel))
    return sort_todos(hits)


def render_todos(items: list[dict], console) -> None:
    """Render markers as a Rich table grouped by kind, with teal accents and a
    count summary in the header. No return value — prints to ``console``."""
    from rich.table import Table

    from .theme import ACCENT, MUTE, SOFT, WARN

    if not items:
        console.print(f"[{ACCENT}]✓ no TODO/FIXME/HACK/XXX/BUG markers — clean.[/{ACCENT}]")
        return

    items = sort_todos(items)

    # Per-kind tally for the header, ordered by urgency then count.
    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    summary = "  ".join(
        f"[{ACCENT}]{k}[/{ACCENT}] [{MUTE}]{counts[k]}[/{MUTE}]"
        for k in sorted(counts, key=lambda k: (priority(k), -counts[k]))
    )
    console.print(f"[bold {ACCENT}]🗒  {len(items)} marker(s)[/bold {ACCENT}]   {summary}")

    table = Table(box=None, padding=(0, 2), header_style=f"bold {ACCENT}")
    table.add_column("kind", style=WARN, no_wrap=True)
    table.add_column("location", style=ACCENT, no_wrap=True)
    table.add_column("note", style=SOFT)

    # Grouped by kind (urgent first); a thin rule between groups keeps the board
    # scannable when several markers pile up.
    last_kind: str | None = None
    for it in items:
        if last_kind is not None and it["kind"] != last_kind:
            table.add_section()
        last_kind = it["kind"]
        note = it.get("text", "") or f"[{MUTE}](no note)[/{MUTE}]"
        table.add_row(it["kind"], f"{it['path']}:{it['line']}", note)
    console.print(table)
