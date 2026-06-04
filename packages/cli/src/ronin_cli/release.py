"""Cut a release — `ronin release [major|minor|patch]`.

Bumps the version everywhere it's declared, generates the changelog section from
the commits since the last tag, and (optionally) creates the git tag — the whole
release chore in one gated command. The semver math + version rewriting are pure
and unit-tested.
"""
from __future__ import annotations

import re
from pathlib import Path

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
# version declarations we know how to rewrite.
_VERSION_PATTERNS = [
    re.compile(r'(__version__\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])'),
    re.compile(r'(^version\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])', re.MULTILINE),
]


def bump_version(version: str, kind: str = "patch") -> str:
    """Return ``version`` bumped by ``kind`` (major|minor|patch). Pure."""
    m = _SEMVER_RE.match(version.strip())
    if not m:
        raise ValueError(f"not a semver: {version!r}")
    major, minor, patch = (int(x) for x in m.groups())
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    elif kind == "patch":
        patch += 1
    else:
        raise ValueError(f"unknown bump kind: {kind!r}")
    return f"{major}.{minor}.{patch}"


def set_version_in_text(text: str, new: str) -> tuple[str, int]:
    """Rewrite any ``__version__``/``version`` declaration to ``new``. Returns
    (text, count_replaced). Pure."""
    count = 0

    def _sub(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}{new}{m.group(3)}"

    for pat in _VERSION_PATTERNS:
        text = pat.sub(_sub, text)
    return text, count


def find_version_files(root: Path | str) -> list[Path]:
    """Files that declare a version we can bump (``__init__.py`` with
    ``__version__``, and ``pyproject.toml``)."""
    rp = Path(root).resolve()
    out: list[Path] = []
    for p in rp.rglob("__init__.py"):
        if any(part in {".venv", "venv", "node_modules", "__pycache__", ".git"} for part in p.parts):
            continue
        try:
            if "__version__" in p.read_text(encoding="utf-8", errors="ignore"):
                out.append(p)
        except OSError:
            continue
    pj = rp / "pyproject.toml"
    if pj.is_file():
        out.append(pj)
    return out


def current_version(files: list[Path]) -> str | None:
    """The first version found across ``files``."""
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in _VERSION_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(2)
    return None
