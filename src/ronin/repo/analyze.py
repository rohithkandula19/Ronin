"""Repository intelligence: what the codebase *is*, derived from one scan.

Every analysis here is a pure function of a :class:`~ronin.context.repomap.RepoScan`
— the un-budgeted walk/parse/rank the map is built from. Nothing here reads the disk
or the network a second time: the scan already read every code file once, so ``map``,
``health``, ``explain`` and ``deadcode`` are just different questions asked of the same
graph. That keeps them cheap (one walk serves all four), deterministic (a scan is a
value), and testable without a model or a fixture server — a test builds a tiny repo,
scans it for real, and asserts on the dataclasses.

The honest-limits rule applies throughout. The import graph is *static*: a module
reached only through ``importlib``, a plugin entry point, or a test runner's discovery
looks orphaned here, so ``deadcode`` and the orphan health signal are labelled
**candidates**, never verdicts. And nothing claims a line count it did not measure —
"size" is expressed as the number of definitions a file exposes (which the scan really
counted), not as LOC it would have to re-read to know.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ronin.context.repomap import RepoScan, SignatureKind

#: Source suffix → human language label, for the ``map`` language breakdown. A suffix
#: outside this table falls back to the suffix itself (minus the dot), so an unmapped
#: language is reported honestly rather than dropped.
LANGUAGE_BY_SUFFIX: Mapping[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".php": "PHP",
    ".scala": "Scala",
    ".lua": "Lua",
    ".sh": "Shell",
}

#: How many top-ranked modules ``map`` lists by default.
DEFAULT_TOP_MODULES = 15

#: A module exposing more than this many definitions is flagged as a large surface —
#: a proxy for "this file is doing too much", using the count the scan already has
#: rather than a line count it would have to re-read to know.
LARGE_API_SIGNATURES = 40


def _suffix(path: str) -> str:
    dot = path.rfind(".")
    slash = path.rfind("/")
    return path[dot:] if dot > slash else ""


def language_of(path: str) -> str:
    """The language label for a repo-relative path."""
    suffix = _suffix(path)
    return LANGUAGE_BY_SUFFIX.get(suffix, suffix.lstrip(".") or "other")


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def is_test_file(path: str) -> bool:
    """A file that exists to test other code — never dead, never an orphan worth flagging."""
    base = _basename(path)
    return (
        "/tests/" in f"/{path}"
        or path.startswith("tests/")
        or base.startswith("test_")
        or base.endswith("_test.py")
        or base == "conftest.py"
    )


def is_entry_point(path: str, signatures: tuple[Any, ...]) -> bool:
    """A file meant to be executed, not imported.

    Detected without re-reading the source: a ``__main__.py`` (the ``python -m pkg``
    entry) or a module that defines a top-level ``main`` function. Both are reached by
    the runtime, not by an ``import``, so both correctly look leaf-like in the graph.
    """
    if _basename(path) == "__main__.py":
        return True
    return any(
        sig.kind is SignatureKind.FUNCTION and sig.name == "main" and not sig.parent
        for sig in signatures
    )


def _is_package_init(path: str) -> bool:
    return _basename(path) in ("__init__.py", "__main__.py")


# --------------------------------------------------------------------------- #
# map
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ModuleRank:
    """One module's place in the import graph."""

    path: str
    rank: float
    inbound: int
    definitions: int


@dataclass(frozen=True, slots=True)
class RepoOverview:
    """``ronin repo map`` — the shape of the tree at a glance."""

    root: str
    total_files: int
    total_definitions: int
    files_by_language: Mapping[str, int]
    entry_points: tuple[str, ...]
    top_modules: tuple[ModuleRank, ...]
    orphan_count: int
    parse_error_count: int


def overview(scan: RepoScan, *, top: int = DEFAULT_TOP_MODULES) -> RepoOverview:
    """The map view: counts, languages, entry points, and the load-bearing modules."""
    by_language: dict[str, int] = {}
    for path in scan.files:
        label = language_of(path)
        by_language[label] = by_language.get(label, 0) + 1
    ranked = sorted(scan.files, key=lambda p: (-scan.ranks.get(p, 0.0), p))
    top_modules = tuple(
        ModuleRank(
            path=path,
            rank=round(scan.ranks.get(path, 0.0), 6),
            inbound=scan.inbound.get(path, 0),
            definitions=len(scan.signatures.get(path, ())),
        )
        for path in ranked[:top]
    )
    entry_points = tuple(
        path for path in scan.files if is_entry_point(path, scan.signatures.get(path, ()))
    )
    orphans = _orphan_candidates(scan)
    return RepoOverview(
        root=str(scan.root),
        total_files=len(scan.files),
        total_definitions=sum(len(sigs) for sigs in scan.signatures.values()),
        files_by_language=dict(sorted(by_language.items(), key=lambda kv: (-kv[1], kv[0]))),
        entry_points=entry_points,
        top_modules=top_modules,
        orphan_count=len(orphans),
        parse_error_count=len(scan.parse_errors),
    )


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HealthSignal:
    """One observation about the repo. Ordered by severity then path when rendered."""

    kind: str
    severity: str  # "warn" | "info"
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class HealthReport:
    """``ronin repo health`` — signals plus a count of each kind."""

    root: str
    signals: tuple[HealthSignal, ...]
    summary: Mapping[str, int]
    file_count: int
    test_file_count: int


def _orphan_candidates(scan: RepoScan) -> tuple[str, ...]:
    """Leaves nothing imports, minus the files that are *supposed* to be leaves.

    Entry points, tests, and package ``__init__``/``__main__`` files are reached by the
    runtime or the test runner, not by an ``import``, so they are leaves by design and
    excluded. What remains is a genuine "imported by nothing" candidate.
    """
    out: list[str] = []
    for path in scan.graph.leaves():
        if is_test_file(path) or _is_package_init(path):
            continue
        if is_entry_point(path, scan.signatures.get(path, ())):
            continue
        out.append(path)
    return tuple(sorted(out))


def health(scan: RepoScan) -> HealthReport:
    """Static health signals, all derived from the scan — no second read of the tree."""
    signals: list[HealthSignal] = []

    for path in sorted(scan.parse_errors):
        detail = scan.parse_errors[path]
        skipped = detail.startswith(("skipped:", "unreadable:"))
        signals.append(
            HealthSignal(
                kind="parse-error",
                severity="info" if skipped else "warn",
                path=path,
                detail=detail,
            )
        )

    for path in _orphan_candidates(scan):
        signals.append(
            HealthSignal(
                kind="orphan-module",
                severity="info",
                path=path,
                detail="imported by nothing in this repo (static analysis — may be a "
                "dynamic/plugin entry point)",
            )
        )

    for path in sorted(scan.files):
        count = len(scan.signatures.get(path, ()))
        if count > LARGE_API_SIGNATURES:
            signals.append(
                HealthSignal(
                    kind="large-surface",
                    severity="warn",
                    path=path,
                    detail=f"{count} definitions (over {LARGE_API_SIGNATURES}) — "
                    "consider splitting",
                )
            )

    test_files = [path for path in scan.files if is_test_file(path)]
    if not test_files:
        signals.append(
            HealthSignal(
                kind="no-tests",
                severity="warn",
                path="",
                detail="no test files found under this tree",
            )
        )

    severity_rank = {"warn": 0, "info": 1}
    ordered = tuple(
        sorted(signals, key=lambda s: (severity_rank.get(s.severity, 2), s.kind, s.path))
    )
    summary: dict[str, int] = {}
    for signal in ordered:
        summary[signal.kind] = summary.get(signal.kind, 0) + 1
    return HealthReport(
        root=str(scan.root),
        signals=ordered,
        summary=dict(sorted(summary.items())),
        file_count=len(scan.files),
        test_file_count=len(test_files),
    )


# --------------------------------------------------------------------------- #
# explain
# --------------------------------------------------------------------------- #


class UnknownPathError(KeyError):
    """The path asked about is not a scanned code file in this repo."""


@dataclass(frozen=True, slots=True)
class Explanation:
    """``ronin repo explain <path>`` — a single file's shape and its neighbours."""

    path: str
    language: str
    rank: float
    inbound: int
    definitions: tuple[str, ...]
    imports: tuple[str, ...]
    imported_by: tuple[str, ...]
    parse_error: str
    is_entry_point: bool
    is_test: bool


def explain(scan: RepoScan, path: str) -> Explanation:
    """Everything the scan knows about one file, including who links to it.

    ``imports`` and ``imported_by`` are the *resolved* graph edges — only links to
    other files in this repo, not third-party packages — so they answer "if I change
    this, what in the tree is affected" (``imported_by``) directly.
    """
    rel = path.strip().lstrip("./")
    if rel not in scan.signatures:
        raise UnknownPathError(rel)
    signatures = scan.signatures.get(rel, ())
    imports = tuple(sorted(target for source, target in scan.graph.edges if source == rel))
    imported_by = tuple(sorted(source for source, target in scan.graph.edges if target == rel))
    return Explanation(
        path=rel,
        language=language_of(rel),
        rank=round(scan.ranks.get(rel, 0.0), 6),
        inbound=scan.inbound.get(rel, 0),
        definitions=tuple(f"{sig.kind.value} {sig.name}" for sig in signatures),
        imports=imports,
        imported_by=imported_by,
        parse_error=scan.parse_errors.get(rel, ""),
        is_entry_point=is_entry_point(rel, signatures),
        is_test=is_test_file(rel),
    )


# --------------------------------------------------------------------------- #
# deadcode
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DeadCodeReport:
    """``ronin repo deadcode`` — import-graph leaves that look unreachable."""

    root: str
    candidates: tuple[str, ...]
    note: str = field(
        default=(
            "Candidates only. These modules are imported by nothing else in the repo "
            "by *static* analysis; a module reached via importlib, a plugin/entry-point, "
            "or test discovery will appear here but is not dead. Verify before deleting."
        )
    )


def deadcode(scan: RepoScan) -> DeadCodeReport:
    """Import-graph leaves, minus the files that are leaves by design."""
    return DeadCodeReport(root=str(scan.root), candidates=_orphan_candidates(scan))


__all__ = [
    "DeadCodeReport",
    "Explanation",
    "HealthReport",
    "HealthSignal",
    "ModuleRank",
    "RepoOverview",
    "UnknownPathError",
    "deadcode",
    "explain",
    "health",
    "is_entry_point",
    "is_test_file",
    "language_of",
    "overview",
]
