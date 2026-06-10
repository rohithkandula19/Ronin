"""Generate a changelog section from git commits — Conventional-Commits aware.

`ronin changelog` reads commit subjects since a ref, groups them by type
(feat / fix / refactor / …), and renders a clean markdown section you can drop
into CHANGELOG.md. The parse + group + render are pure and unit-tested; the
command just supplies `git log` output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# type → section heading, in display order. Anything else lands under "Other".
_SECTIONS: list[tuple[str, str]] = [
    ("feat", "Added"),
    ("fix", "Fixed"),
    ("perf", "Performance"),
    ("refactor", "Changed"),
    ("docs", "Docs"),
    ("test", "Tests"),
    ("chore", "Chores"),
]
_KNOWN = {t for t, _ in _SECTIONS}

_COMMIT_RE = re.compile(
    r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$")


@dataclass
class Commit:
    type: str
    scope: str | None
    subject: str
    breaking: bool


def parse_commit(subject: str) -> Commit:
    """Parse a Conventional-Commits subject. Non-conforming lines become type
    'other' with the whole line as the subject. Pure."""
    m = _COMMIT_RE.match(subject.strip())
    if not m:
        return Commit(type="other", scope=None, subject=subject.strip(), breaking=False)
    t = m.group("type").lower()
    return Commit(type=t if t in _KNOWN else "other",
                  scope=m.group("scope"),
                  subject=m.group("subject").strip(),
                  breaking=bool(m.group("breaking")))


def group_commits(subjects: list[str]) -> dict[str, list[Commit]]:
    """Group parsed commits by type (preserving order within a type). Pure."""
    groups: dict[str, list[Commit]] = {}
    for s in subjects:
        if not s.strip():
            continue
        c = parse_commit(s)
        groups.setdefault(c.type, []).append(c)
    return groups


def render_changelog(subjects: list[str], *, version: str = "Unreleased",
                     date: str = "") -> str:
    """Render a markdown changelog section from commit subjects. Pure."""
    groups = group_commits(subjects)
    header = f"## [{version}]" + (f" — {date}" if date else "")
    lines = [header, ""]

    breaking = [c for cs in groups.values() for c in cs if c.breaking]
    if breaking:
        lines.append("### ⚠ BREAKING CHANGES")
        for c in breaking:
            lines.append(f"- {_fmt(c)}")
        lines.append("")

    for typ, heading in _SECTIONS:
        items = [c for c in groups.get(typ, []) if not c.breaking]
        if not items:
            continue
        lines.append(f"### {heading}")
        lines += [f"- {_fmt(c)}" for c in items]
        lines.append("")

    other = [c for c in groups.get("other", []) if not c.breaking]
    if other:
        lines.append("### Other")
        lines += [f"- {_fmt(c)}" for c in other]
        lines.append("")

    if len(lines) <= 2:
        lines.append("_No notable changes._")
    return "\n".join(lines).rstrip() + "\n"


def _fmt(c: Commit) -> str:
    scope = f"**{c.scope}:** " if c.scope else ""
    return f"{scope}{c.subject}"
