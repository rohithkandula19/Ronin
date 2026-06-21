"""``ronin scan`` — sweep the repo for leaked credentials and report each one as
``file:line + kind``, **never printing the secret value itself**.

This is the user-facing protective scan. It reuses the same provider-prefixed
credential pattern set as the write-time guard (``secret_guard`` →
``ronin_hardening.secret_scanner``) so what blocks a commit is exactly what this
surfaces, plus a PEM private-key block matcher (the highest-bleed leak of all).

Layering:
    find_secrets(text, path) -> list[dict]   PURE, line-level, fully testable.
    render_findings(findings, console)       PURE-ish rich rendering (a table).
    scan_repo(root) -> list[dict]            IMPURE — walks the tree (filesystem).

SAFETY CONTRACT (the whole point of this module):
    The full secret value MUST NEVER appear in a return value or in rendered
    output. A finding carries only {path, line, kind, hint} where ``hint`` is a
    *masked* fragment (e.g. ``sk-…f00d``) — a tiny prefix + last-4 with the
    high-entropy middle elided — or just the kind. ``find_secrets`` asserts this
    invariant on every hint before returning, so a regression can't silently
    start leaking. Over-reporting (a false positive) is acceptable; leaking is
    not.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Reuse the canonical, low-false-positive credential pattern set that the
# write/commit guard uses (anthropic/openai sk-, stripe sk_/rk_/pk_, github
# ghp_/gh*_, slack xoxb-/xoxp-/xapp-, AWS AKIA, linear lin_api_, jwt, fernet,
# notion secret_, resend re_). Single source of truth → the scan and the guard
# can never drift apart.
from ronin_hardening.secret_scanner import SECRET_PATTERNS as _BASE_PATTERNS

# A PEM private-key block is the single highest-impact leak (SSH / TLS / signing
# keys). The base set is tuned for inline provider keys and omits it, so add it
# here. We match only the BEGIN header line — enough to locate + classify the
# leak without ever capturing the key body.
_PRIVATE_KEY_RE = (
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
)

# (kind, compiled-regex). Kinds mirror the hardening labels so a finding here
# means the same thing as a guard finding.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = (
    [(label, re.compile(rx)) for (label, rx, _placeholder) in _BASE_PATTERNS]
    + [("private-key", re.compile(_PRIVATE_KEY_RE))]
)

# Substrings marking an obvious example / placeholder rather than a live key.
# Mirrors secret_guard, but deliberately drops its bare-repetition markers
# (``XXXXXX`` / ``1234567890`` / ``DEADBEEF``): a real high-entropy key can
# legitimately contain a run of x's or digits (the canonical scan smoke-test
# key is literally ``sk-`` + 40 x's), so those would cause false *negatives* —
# the one failure mode this tool must never have. We keep only word markers that
# unambiguously signal a doc/example value. Compared against the upper-cased
# match (case-insensitive). "better to over-report than to leak."
_PLACEHOLDER_MARKERS = (
    "EXAMPLE", "PLACEHOLDER", "YOUR_", "YOURKEY", "YOUR-", "REDACTED",
    "TEST_TOKEN", "FAKEKEY", "DUMMY", "CHANGEME", "CHANGE_ME", "SAMPLE",
    "NOTAREAL", "INSERT_", "<YOUR",
)
# Inline allow-pragmas — drop one on the same line as a known-safe value to
# suppress it (the standard detect-secrets-style escape hatch).
_ALLOW_PRAGMAS = ("ronin:allow-secret", "pragma: allowlist secret", "noqa: secret")

# Directories we never descend into — vendored deps, VCS internals, build
# output, caches. Pruned by *name* at any depth.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist",
    "build", ".next", ".nuxt", "target", ".tox", ".gradle", "vendor",
    ".idea", ".ronin", ".csk", "site-packages", ".terraform",
}

# Only sniff text-ish files. Empty suffix is allowed (covers ``.env``-style and
# extension-less config / dotfiles). Binary blobs are skipped wholesale.
_TEXT_SUFFIXES = {
    "", ".py", ".pyi", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".go",
    ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".c", ".h", ".cpp", ".cc",
    ".swift", ".scala", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".sql",
    ".toml", ".json", ".jsonc", ".yaml", ".yml", ".xml", ".env", ".ini",
    ".cfg", ".conf", ".properties", ".md", ".mdx", ".txt", ".rst", ".tf",
    ".tfvars", ".pem", ".key", ".crt", ".tpl", ".template", ".dockerfile",
    ".gradle", ".groovy", ".lua", ".pl", ".r", ".dart", ".vue", ".svelte",
    ".html", ".htm", ".gitconfig", ".npmrc", ".netrc",
}

# Don't read anything bigger than this — a sane cap that still covers real
# source/config files while skipping data dumps, media and giant lockfiles.
_MAX_FILE_BYTES = 1_500_000


# --------------------------------------------------------------------------- #
# Masking — turn a raw match into a hint that locates the secret without ever
# revealing it.
# --------------------------------------------------------------------------- #
def _mask(kind: str, match: str) -> str:
    """A safe, human hint for a matched secret: a short prefix + last-4 with the
    high-entropy middle elided, e.g. ``sk-…a1b2``. Falls back to just ``kind``
    when the value is too short to reveal anything safely.

    Guarantees (load-bearing — ``find_secrets`` re-checks them):
      * the returned hint is never a substring of ``match`` (so it can't be the
        secret), and ``match`` is never a substring of the hint;
      * at most 4 trailing chars are shown, and only when the match is long
        enough that 4 chars are a negligible fraction of it.
    """
    n = len(match)
    # Too short to safely show any fragment — name the kind only.
    if n < 12:
        return kind
    # Prefix: keep the human-recognisable provider marker, but never more than a
    # few chars and never the whole thing.
    prefix = match[:4]
    last4 = match[-4:]
    hint = f"{prefix}…{last4}"
    # Airtight fallback: if for any reason the masked fragment is still a
    # substring of the real value (or vice-versa), reveal nothing.
    if hint in match or match in hint:
        return kind
    return hint


def _line_number(text: str, index: int) -> int:
    """1-based line number of character offset ``index`` in ``text``."""
    return text.count("\n", 0, index) + 1


def _line_text(text: str, index: int) -> str:
    """The full source line containing offset ``index`` (no trailing newline)."""
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start:end if end != -1 else len(text)]


def _is_suppressed(match: str, line: str) -> bool:
    """True when a match should be ignored: an obvious placeholder/example value,
    or a line carrying an allow-pragma."""
    up = match.upper()
    if any(marker in up for marker in _PLACEHOLDER_MARKERS):
        return True
    low = line.lower()
    if any(pragma in low for pragma in _ALLOW_PRAGMAS):
        return True
    return False


# --------------------------------------------------------------------------- #
# PURE core — no I/O, fully deterministic, unit-tested.
# --------------------------------------------------------------------------- #
def find_secrets(text: str, path: str) -> list[dict[str, Any]]:
    """Scan ``text`` for leaked credentials and return one finding per match.

    Each finding is ``{path, line, kind, hint}``:
        path  — the ``path`` passed in (caller-relative; this fn does no I/O).
        line  — 1-based line number of the match.
        kind  — the credential type (e.g. ``openai-key``, ``private-key``).
        hint  — a *masked* fragment (``sk-…a1b2``) or just ``kind`` — NEVER the
                full secret.

    Findings are sorted by (line, kind). Overlapping matches from different
    patterns at the same offset are de-duplicated (first/broadest wins), so a
    single key isn't reported twice. SAFETY: the raw matched value is asserted
    absent from every emitted hint before returning.
    """
    if not text:
        return []

    # Collect candidate spans across all patterns, then resolve overlaps by
    # preferring the earliest start / longest match — same strategy the
    # hardening scanner uses so behaviour is consistent.
    spans: list[tuple[int, int, str]] = []  # (start, end, kind)
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), kind))
    spans.sort(key=lambda s: (s[0], -s[1]))

    findings: list[dict[str, Any]] = []
    last_end = -1
    for start, end, kind in spans:
        if start < last_end:  # overlaps an already-accepted span → skip
            continue
        match = text[start:end]
        line_text = _line_text(text, start)
        if _is_suppressed(match, line_text):
            last_end = end
            continue
        hint = _mask(kind, match)
        # Hard safety gate: the full secret must not survive into the finding.
        # If masking ever failed, degrade to the bare kind rather than leak.
        if match in hint:
            hint = kind
        findings.append({
            "path": path,
            "line": _line_number(text, start),
            "kind": kind,
            "hint": hint,
        })
        last_end = end

    findings.sort(key=lambda f: (f["line"], f["kind"]))
    return findings


# --------------------------------------------------------------------------- #
# Rendering — a rich table. Never prints a value, only kind + location + hint.
# --------------------------------------------------------------------------- #
def render_findings(findings: list[dict[str, Any]], console: Any) -> None:
    """Render ``findings`` to ``console`` as a rich table (file:line + kind +
    masked hint, red accents) with a ``N potential secret(s)`` header — or a
    green ``no secrets found`` line when the list is empty.

    Imports rich lazily so the pure ``find_secrets`` path has zero rich cost.
    """
    from rich import box
    from rich.table import Table

    # Brand palette (mirrors theme.py: ERR rose / OK green / MUTE slate). Imported
    # by value to keep this module importable even if theme isn't on the path.
    err, ok, mute = "#f7768e", "#9ece6a", "#6b7089"

    if not findings:
        console.print(f"[{ok}]✓ no secrets found[/{ok}]")
        return

    n = len(findings)
    files = len({f["path"] for f in findings})
    header = (f"[bold {err}]⚠ {n} potential secret{'s' if n != 1 else ''} "
              f"found[/bold {err}] [dim]across {files} file"
              f"{'s' if files != 1 else ''}[/dim]")
    console.print(header)

    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False,
                  header_style=f"bold {mute}")
    table.add_column("location", style=err, no_wrap=True)
    table.add_column("kind", style="bold")
    table.add_column("hint", style=mute)
    for f in findings:
        table.add_row(f"{f['path']}:{f['line']}", str(f["kind"]), str(f["hint"]))
    console.print(table)
    console.print(f"[{mute}]hints are masked — no secret values are shown. "
                  f"Rotate any real key above and remove it from the repo.[/{mute}]")


# --------------------------------------------------------------------------- #
# Ignore handling (gitignore-style, name-level, dependency-free) — mirrors
# repo_index._load_ignore_names so the walk behaves like the rest of ronin.
# --------------------------------------------------------------------------- #
def _load_ignore_names(root: Path) -> set[str]:
    """Directory/file *names* to prune, from ``.gitignore`` / ``.roninignore`` on
    top of the built-in skip set. Name-level (gitignore-ish): a line like
    ``dist/`` or ``node_modules`` prunes that name at any depth. Patterns with
    interior separators or globs are skipped (the built-in set covers the rest).
    """
    names: set[str] = set(_SKIP_DIRS)
    for fname in (".gitignore", ".roninignore"):
        p = root / fname
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            token = line.strip("/").split("/")[-1]
            if token and "*" not in token and "?" not in token:
                names.add(token)
    return names


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_SUFFIXES


def _read_text_capped(path: Path) -> str | None:
    """Read a text file, skipping it if it's over the size cap or looks binary.
    Returns None on any failure (unreadable, too big, binary)."""
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:  # NUL byte → binary; don't scan
        return None
    return data.decode("utf-8", errors="ignore")


# --------------------------------------------------------------------------- #
# IMPURE — walk the repo. Delegates every match decision to find_secrets, so the
# safety contract holds for the whole-repo path too.
# --------------------------------------------------------------------------- #
def scan_repo(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Walk ``root`` and return all secret findings (``{path, line, kind, hint}``,
    paths relative to ``root``). Skips VCS/vendor/build/cache dirs, honours
    ``.gitignore``/``.roninignore`` names, scans text files only, and caps file
    size. Findings are sorted by (path, line)."""
    root_path = Path(root).resolve()
    ignore_names = _load_ignore_names(root_path)
    findings: list[dict[str, Any]] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Prune ignored / hidden directories in place so we never descend into
        # them (big perf + correctness win on real repos).
        dirnames[:] = [d for d in dirnames
                       if d not in ignore_names and not d.startswith(".")]
        for fn in filenames:
            fpath = Path(dirpath) / fn
            if not _is_text_file(fpath):
                continue
            text = _read_text_capped(fpath)
            if not text:
                continue
            try:
                rel = str(fpath.relative_to(root_path))
            except ValueError:
                rel = str(fpath)
            findings.extend(find_secrets(text, rel))

    findings.sort(key=lambda f: (f["path"], f["line"]))
    return findings
