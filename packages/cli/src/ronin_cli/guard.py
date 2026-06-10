"""Intent / scope-creep guard — `ronin guard`.

Before you commit, ronin checks the working diff for the things a reviewer
catches but the author misses:

1. **Debug / secret leftovers** in the lines you *added* — stray `breakpoint()`,
   `console.log`, `import pdb`, unresolved merge markers, `TODO/FIXME`, and a few
   obvious hardcoded-secret shapes (AWS keys, private-key blocks).
2. **Scope creep** — files touched that don't match the task you set out to do
   (an optional LLM pass against an `--intent` or your last commit subject).

The diff parsing and pattern scan are pure and unit-tested; the intent match is
an optional, read-only LLM step. CI-friendly: exits non-zero on high-severity
findings (merge markers, secrets).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# (kind, compiled regex, severity) — matched against ADDED diff lines only.
# severity "high" → CI failure; "warn" → surfaced but non-blocking.
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("merge-marker", re.compile(r"^(<{7}|={7}|>{7})(\s|$)"), "high"),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "high"),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "high"),
    ("breakpoint", re.compile(r"\bbreakpoint\s*\(\)"), "warn"),
    ("pdb", re.compile(r"\b(import pdb|pdb\.set_trace\s*\(|set_trace\s*\(\))"), "warn"),
    ("debugger", re.compile(r"(^|\s)debugger\s*;?\s*$"), "warn"),
    ("console-log", re.compile(r"\bconsole\.(log|debug|trace)\s*\("), "warn"),
    ("todo", re.compile(r"\b(TODO|FIXME|XXX|HACK)\b"), "warn"),
]

# diff hunk-header file paths: `+++ b/path/to/file` (ignore the /dev/null delete side).
_PLUS_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")


@dataclass
class Finding:
    file: str
    kind: str
    severity: str
    text: str


@dataclass
class GuardResult:
    findings: list[Finding] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def high(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "high"]


def changed_files(diff: str) -> list[str]:
    """Files with additions in a unified diff (the `+++ b/...` side)."""
    out: list[str] = []
    for line in (diff or "").splitlines():
        m = _PLUS_FILE_RE.match(line)
        if m and m.group(1) != "/dev/null" and m.group(1) not in out:
            out.append(m.group(1))
    return out


def iter_added_lines(diff: str):
    """Yield (file, added_text) for every `+` line, tracking the current file.

    Skips the `+++` header line itself; yields the content WITHOUT the leading
    `+` so patterns match the real source text.
    """
    current = "?"
    for line in (diff or "").splitlines():
        m = _PLUS_FILE_RE.match(line)
        if m:
            current = m.group(1)
            continue
        if line.startswith("+") and not line.startswith("+++"):
            yield current, line[1:]


def scan_added(diff: str) -> list[Finding]:
    """Scan added lines for debug/secret leftovers. Pure — no I/O."""
    findings: list[Finding] = []
    for file, text in iter_added_lines(diff):
        for kind, rx, severity in _PATTERNS:
            if rx.search(text):
                findings.append(Finding(file=file, kind=kind, severity=severity,
                                        text=text.strip()[:160]))
    return findings


def collect_diff(root: Path | str) -> str:
    """Working-tree diff vs HEAD (all tracked changes, staged or not)."""
    try:
        proc = subprocess.run(["git", "-C", str(Path(root).resolve()), "diff", "HEAD"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def run_guard(root: Path | str, *, diff: str | None = None) -> GuardResult:
    """Collect the diff (unless supplied) and scan it. The pure scan is split out
    so tests can drive it with a literal diff; this wires in git."""
    d = collect_diff(root) if diff is None else diff
    if not d.strip():
        return GuardResult(note="no changes against HEAD")
    return GuardResult(findings=scan_added(d), changed_files=changed_files(d))
