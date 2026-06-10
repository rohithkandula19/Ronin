"""Architecture diagrams — `ronin diagram`.

Extracts the real internal import graph of a Python package (which module imports
which sibling) and renders it as a Mermaid graph you can paste into Markdown/
GitHub, plus a few structural stats (most-depended-on modules, leaf modules). No
model required — it's deterministic from the code. All pure + unit-tested.
"""
from __future__ import annotations

import re
from pathlib import Path

_REL_IMPORT_RE = re.compile(r"^\s*from\s+\.(\w+)\s+import", re.MULTILINE)
_REL_IMPORT2_RE = re.compile(r"^\s*from\s+\.\s+import\s+(.+)$", re.MULTILINE)


def parse_internal_imports(text: str, known: set[str]) -> set[str]:
    """Sibling modules ``text`` imports (``from .x import …`` / ``from . import x``)
    limited to names in ``known``. Pure."""
    deps: set[str] = set()
    for m in _REL_IMPORT_RE.finditer(text or ""):
        if m.group(1) in known:
            deps.add(m.group(1))
    for m in _REL_IMPORT2_RE.finditer(text or ""):
        for name in re.split(r"[,\s]+", m.group(1)):
            name = name.strip().strip("()")
            if name in known:
                deps.add(name)
    return deps


def import_graph(pkg_dir: Path | str) -> dict[str, set[str]]:
    """Map each module (a .py file stem in ``pkg_dir``) → the sibling modules it
    imports. Pure given the directory."""
    pkg = Path(pkg_dir)
    modules = {p.stem: p for p in pkg.glob("*.py") if p.stem != "__init__"}
    known = set(modules)
    graph: dict[str, set[str]] = {}
    for name, path in modules.items():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        graph[name] = parse_internal_imports(text, known) - {name}
    return graph


def to_mermaid(graph: dict[str, set[str]], *, title: str = "") -> str:
    """Render the graph as a Mermaid ``graph LR``. Pure."""
    lines = ["```mermaid", "graph LR"]
    if title:
        lines.insert(1, f"%% {title}")
    edges = sorted((m, d) for m, ds in graph.items() for d in sorted(ds))
    for m, d in edges:
        lines.append(f"  {m} --> {d}")
    # isolated modules (no edges at all) still appear as nodes
    connected = {m for m, d in edges} | {d for m, d in edges}
    for m in sorted(set(graph) - connected):
        lines.append(f"  {m}")
    lines.append("```")
    return "\n".join(lines)


def stats(graph: dict[str, set[str]]) -> dict:
    """Structural facts: most-depended-on modules + leaves. Pure."""
    indeg: dict[str, int] = {m: 0 for m in graph}
    for deps in graph.values():
        for d in deps:
            indeg[d] = indeg.get(d, 0) + 1
    most = sorted(indeg.items(), key=lambda kv: kv[1], reverse=True)
    leaves = sorted(m for m, deps in graph.items() if not deps)
    return {"modules": len(graph),
            "edges": sum(len(d) for d in graph.values()),
            "most_depended": [m for m, n in most if n > 0][:5],
            "leaves": leaves[:8]}


def detect_package(root: Path | str) -> Path | None:
    """Pick the Python package with the most modules (the codebase's heart)."""
    root_path = Path(root).resolve()
    best, best_n = None, 0
    for init in root_path.rglob("__init__.py"):
        if any(part in {".venv", "venv", "node_modules", "__pycache__", ".git"}
               for part in init.parts):
            continue
        d = init.parent
        n = len(list(d.glob("*.py")))
        if n > best_n:
            best, best_n = d, n
    return best
