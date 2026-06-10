"""Dependency audit — `ronin deps`.

Cross-checks the third-party packages your code actually imports against the ones
your project declares (pyproject / requirements.txt): flags declared-but-unused
deps and used-but-undeclared imports (stdlib-aware). Pure parsing, unit-tested.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin"}

# A handful of common import-name → distribution-name aliases.
_ALIASES = {
    "yaml": "pyyaml", "PIL": "pillow", "cv2": "opencv-python", "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv", "sklearn": "scikit-learn", "dateutil": "python-dateutil",
}


def _norm(name: str) -> str:
    return re.split(r"[<>=!~\[ ]", name.strip(), maxsplit=1)[0].strip().lower().replace("_", "-")


def imported_top_level(source: str) -> set[str]:
    """Top-level imported package names in ``source`` (first segment). Pure."""
    out: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                out.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:    # absolute import only
                out.add(node.module.split(".")[0])
    return out


def parse_declared(pyproject: str = "", requirements: str = "") -> set[str]:
    """Declared distribution names from pyproject ``dependencies`` + a
    requirements file. Normalized. Pure."""
    declared: set[str] = set()
    # pyproject: dependencies = ["a>=1", "b"] (+ optional groups, best-effort)
    for m in re.finditer(r'["\']([A-Za-z0-9_.\-]+(?:\[[^\]]*\])?[^"\']*)["\']', pyproject):
        val = m.group(1)
        if re.match(r"^[A-Za-z0-9_.\-]", val) and any(c.isalpha() for c in val):
            declared.add(_norm(val))
    for line in requirements.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            declared.add(_norm(line))
    return {d for d in declared if d}


def _stdlib() -> set[str]:
    names = getattr(sys, "stdlib_module_names", set())
    return set(names) | {"__future__"}


def audit(root: Path | str, *, declared: set[str] | None = None) -> dict:
    """Return {'used': set, 'undeclared': sorted, 'unused': sorted}. ``declared``
    can be injected (tests); otherwise read from pyproject/requirements."""
    rp = Path(root).resolve()
    used: set[str] = set()
    for p in rp.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        try:
            used |= imported_top_level(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    if declared is None:
        pj = (rp / "pyproject.toml")
        req = next((f for f in [rp / "requirements.txt"] if f.is_file()), None)
        declared = parse_declared(pj.read_text(encoding="utf-8", errors="ignore") if pj.is_file() else "",
                                  req.read_text(encoding="utf-8", errors="ignore") if req else "")
    std = _stdlib()
    # third-party imports = used minus stdlib minus the repo's own top-level pkgs
    own = {p.name for p in rp.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    third_party = {_norm(u) for u in used if u not in std and u not in own and u}
    undeclared = sorted(t for t in third_party if t not in declared
                        and _ALIASES.get(t, t) not in declared)
    used_norm = {_ALIASES.get(t, t) for t in third_party} | third_party
    unused = sorted(d for d in declared if d not in used_norm)
    return {"used": third_party, "undeclared": undeclared, "unused": unused}
