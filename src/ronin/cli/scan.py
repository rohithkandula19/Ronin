"""``ronin scan`` — sweep a tree, or its history, for credentials that got committed.

The I/O half of :mod:`ronin.safety.credentials`: walk, read, run ``git``, render. Every
decision about *whether a string is a secret* belongs over there and none of it is
repeated here, which is what keeps the safety contract — a location, never a value —
true for the whole-tree path and not just the string path.

Three seams are injected (``read_tree``, ``read_diff``, ``staged``) so the render
paths, the exit codes and the "git is not installed" path are all testable with no
repository and no subprocess, as the rest of ``cli/`` does.

**It exits 1 when it finds something**, which is the difference between a report and a
gate. A pre-commit hook or a CI step is the place this is most useful and both read the
status, not the text — so ``--quiet`` prints nothing at all and still refuses.

Ported from v1's ``ronin1 dev scan``. Three changes worth naming: the renderer is plain
text rather than a Rich table, because this tree ships no hard dependencies and a
credential scanner is the last thing that should be unavailable on a bare install;
``--output-format json`` is new, because a scan that a CI step can parse is a scan a CI
step can act on; and history findings render through the same path as working-tree ones
instead of a second bespoke loop that had drifted to a different layout.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from ronin.safety.credentials import Finding, find_secrets, find_secrets_in_diff

#: Never descended into, pruned by *name* at any depth: version control internals,
#: vendored dependencies, build output, caches. A hit inside ``node_modules`` is a
#: report about somebody else's repository.
SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        ".tox",
        ".gradle",
        "vendor",
        ".idea",
        ".ronin",
        ".csk",
        "site-packages",
        ".terraform",
    }
)

#: Suffixes worth reading. The empty string is in the set on purpose — it covers
#: ``.env``-style and extension-less config, which is where keys most often are.
TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "",
        ".py",
        ".pyi",
        ".js",
        ".cjs",
        ".mjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".rb",
        ".php",
        ".cs",
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".swift",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".sql",
        ".toml",
        ".json",
        ".jsonc",
        ".yaml",
        ".yml",
        ".xml",
        ".env",
        ".ini",
        ".cfg",
        ".conf",
        ".properties",
        ".md",
        ".mdx",
        ".txt",
        ".rst",
        ".tf",
        ".tfvars",
        ".pem",
        ".key",
        ".crt",
        ".tpl",
        ".template",
        ".dockerfile",
        ".gradle",
        ".groovy",
        ".lua",
        ".pl",
        ".r",
        ".dart",
        ".vue",
        ".svelte",
        ".html",
        ".htm",
        ".gitconfig",
        ".npmrc",
        ".netrc",
    }
)

#: Files larger than this are skipped. Real source and config are far below it; data
#: dumps, media and lockfiles are above, and reading them buys nothing but seconds.
MAX_FILE_BYTES: Final = 1_500_000

#: Printed under a non-empty report. The scan is only half the remedy: a key that has
#: been in a repository has to be assumed read, so rotation comes before deletion.
ADVICE: Final = (
    "hints are masked — no secret values are shown above.\n"
    "Rotate every real key listed, then remove it from the tree. A key that was "
    "committed must be treated as disclosed even after it is deleted, because the blob "
    "survives in git history — run `ronin scan --history` to see what is still there.\n"
    "Add `# ronin:allow-secret` to a line to silence a false positive."
)

TreeReader = Callable[[Path], Iterable[tuple[str, str]]]
DiffReader = Callable[[Path, int, str], str | None]
StagedReader = Callable[[Path], Sequence[str] | None]


@dataclass(frozen=True, slots=True)
class ScanOptions:
    """A parsed ``ronin scan``. Pure data; the root is resolved in dispatch."""

    root: Path
    history: bool = False
    staged: bool = False
    since: str = ""
    max_commits: int = 0
    quiet: bool = False
    as_json: bool = False


def ignore_names(root: Path) -> set[str]:
    """Directory and file *names* to prune: :data:`SKIP_DIRS` plus whatever the
    repository's own ignore files name at the name level.

    Name-level and not path-level on purpose. ``dist/`` prunes ``dist`` at any depth,
    which is what a reader of a ``.gitignore`` expects of that line and is right often
    enough; a pattern with an interior separator or a glob is left alone rather than
    approximated, because a scanner that quietly skips a directory it was not asked to
    skip is a scanner that misses the key in it.
    """
    names = set(SKIP_DIRS)
    for filename in (".gitignore", ".roninignore"):
        path = root / filename
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for raw in lines:
            entry = raw.strip()
            if not entry or entry.startswith(("#", "!")):
                continue
            token = entry.strip("/").split("/")[-1]
            if token and "*" not in token and "?" not in token:
                names.add(token)
    return names


def readable(path: Path) -> str | None:
    """The text of ``path``, or ``None`` when it should not be scanned.

    Refuses in this order: wrong suffix, over the size cap, unreadable, binary. The
    NUL-byte test comes last because it needs the bytes, and the three cheap refusals
    before it mean most of a tree never gets read at all.
    """
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="ignore")


def walk_tree(root: Path) -> Iterable[tuple[str, str]]:
    """``(relative path, text)`` for every scannable file under ``root``.

    A generator rather than a list: a large repository's worth of file contents does
    not need to exist at once, and the caller consumes each one into findings.
    """
    resolved = root.resolve()
    pruned = ignore_names(resolved)
    for dirpath, dirnames, filenames in os.walk(resolved):
        dirnames[:] = [name for name in dirnames if name not in pruned and not name.startswith(".")]
        for filename in filenames:
            path = Path(dirpath) / filename
            text = readable(path)
            if not text:
                continue
            try:
                yield str(path.relative_to(resolved)), text
            except ValueError:  # pragma: no cover - os.walk yields paths under root
                yield str(path), text


def git_diff(root: Path, max_commits: int, since: str) -> str | None:
    """``git log -p`` over ``root``, or ``None`` when git cannot answer.

    ``None`` rather than an empty string, and the distinction is the point: no git, or
    not a repository, must not render as "no secrets in your history". The caller says
    which one happened.
    """
    command = ["git", "-C", str(root), "log", "-p", "--no-color", "--no-merges"]
    if max_commits > 0:
        command += ["-n", str(max_commits)]
    if since:
        command += ["--since", since]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, errors="ignore", check=False
        )
    except (OSError, ValueError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def git_staged(root: Path) -> Sequence[str] | None:
    """Paths staged for commit, or ``None`` when git cannot answer."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
            errors="ignore",
            check=False,
        )
    except (OSError, ValueError):
        return None
    if completed.returncode != 0:
        return None
    return [line for line in completed.stdout.splitlines() if line.strip()]


def render(findings: Sequence[Finding], *, scope: str) -> str:
    """The report: a header, one line per finding, and what to do about it.

    Plain text and aligned by hand rather than a table library, because this tree
    declares no hard dependencies and the one command whose absence is a security
    problem should not be the one that needs an extra installed.
    """
    if not findings:
        return f"ronin scan: no secrets found in {scope}\n"

    files = len({finding.path for finding in findings})
    plural = "" if len(findings) == 1 else "s"
    header = (
        f"ronin scan: {len(findings)} potential secret{plural} in {scope}, "
        f"across {files} file{'' if files == 1 else 's'}\n"
    )
    locations = [
        f"{finding.commit[:10] + ' ' if finding.commit else ''}{finding.path}:{finding.line}"
        for finding in findings
    ]
    width = max(len(location) for location in locations)
    body = "".join(
        f"  {location:<{width}}  {finding.kind}  ({finding.hint})\n"
        for location, finding in zip(locations, findings, strict=True)
    )
    return f"{header}\n{body}\n{ADVICE}\n"


def _as_json(findings: Sequence[Finding], *, scope: str) -> str:
    payload = {
        "scope": scope,
        "count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    return json.dumps(payload, indent=2) + "\n"


def _staged_findings(options: ScanOptions, staged: StagedReader) -> list[Finding] | None:
    paths = staged(options.root)
    if paths is None:
        return None
    root = options.root.resolve()
    pruned = ignore_names(root)
    findings: list[Finding] = []
    for relative in paths:
        path = root / relative
        if any(part in pruned for part in Path(relative).parts):
            continue
        text = readable(path)
        if not text:
            continue
        findings.extend(find_secrets(text, relative))
    findings.sort(key=lambda finding: (finding.path, finding.line))
    return findings


def run_scan(
    options: ScanOptions,
    *,
    read_tree: TreeReader = walk_tree,
    read_diff: DiffReader = git_diff,
    staged: StagedReader = git_staged,
) -> tuple[int, str, str]:
    """Run one scan. Returns ``(exit_code, stdout, stderr)``.

    ``1`` means secrets were found — the status is the gate, and ``--quiet`` exists so
    a hook can use it without printing anything. ``2`` means the scan could not run
    (git missing, or not a repository) and is *not* the same as a clean result: a
    caller that treats "could not look" as "nothing there" is the failure this
    separates out.
    """
    if options.history:
        diff = read_diff(options.root, options.max_commits, options.since)
        if diff is None:
            return (
                2,
                "",
                (
                    "ronin scan: --history needs git and a repository; `git log` did not "
                    f"run in {options.root}. Nothing was scanned — this is not a clean "
                    "result.\n"
                ),
            )
        findings: list[Finding] = find_secrets_in_diff(diff)
        scope = "git history"
    elif options.staged:
        collected = _staged_findings(options, staged)
        if collected is None:
            return (
                2,
                "",
                (
                    "ronin scan: --staged needs git and a repository; `git diff --cached` "
                    f"did not run in {options.root}. Nothing was scanned — this is not a "
                    "clean result.\n"
                ),
            )
        findings, scope = collected, "staged changes"
    else:
        findings = [
            finding
            for relative, text in read_tree(options.root)
            for finding in find_secrets(text, relative)
        ]
        findings.sort(key=lambda finding: (finding.path, finding.line))
        scope = "the working tree"

    code = 1 if findings else 0
    if options.quiet:
        return code, "", ""
    body = _as_json(findings, scope=scope) if options.as_json else render(findings, scope=scope)
    return code, body, ""


__all__ = [
    "ADVICE",
    "MAX_FILE_BYTES",
    "SKIP_DIRS",
    "TEXT_SUFFIXES",
    "ScanOptions",
    "git_diff",
    "git_staged",
    "ignore_names",
    "readable",
    "render",
    "run_scan",
    "walk_tree",
]
