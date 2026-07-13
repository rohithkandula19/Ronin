"""Repository dependency-graph intelligence (Ronin v1.1 / Phase 6).

Builds a MODULE-level import graph of a repository from Python source using the
stdlib :mod:`ast` (no new dependencies), then derives architecture-level views on
top of it: framework/stack detection, package structure, dependency metrics, and
circular-import detection. Rendered as text, JSON, or Mermaid.

This composes with the existing repo index (``repo_index.py`` builds a FILE-level
search index); the graph adds the RELATIONSHIP layer the index does not have.
Only *intra-repository* imports become edges — third-party/stdlib imports are
counted for stack detection but are not nodes, so the graph stays about YOUR
architecture, not the ecosystem.
"""
from __future__ import annotations

import ast
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

_IGNORE_DIRS = frozenset({
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__", "dist", "build",
    ".ronin", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "site-packages",
    ".eggs", ".idea", ".vscode", "htmlcov", ".next", "target",
})


def _iter_py_files(root: Path):
    for p in root.rglob("*.py"):
        if any(part in _IGNORE_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _module_name(path: Path) -> str:
    """Dotted module name for a ``.py`` file. Walks up while parent dirs are
    packages (contain ``__init__.py``); the first non-package parent is the source
    root. Handles ``src/`` layouts: ``.../src/pkg/mod.py`` -> ``pkg.mod``. A
    package's ``__init__.py`` becomes the package name itself."""
    parts: list[str] = [] if path.stem == "__init__" else [path.stem]
    pkg = path.parent
    while (pkg / "__init__.py").exists():
        parts.insert(0, pkg.name)
        pkg = pkg.parent
    return ".".join(parts)


@dataclass
class ImportGraph:
    """A module-level intra-repo import graph. ``edges[m]`` is the set of internal
    modules that ``m`` imports. ``files[m]`` is the module's repo-relative path.
    ``external[m]`` is the set of third-party/stdlib top-level packages it imports
    (used for stack detection, not graphed)."""
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    files: dict[str, str] = field(default_factory=dict)
    external: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def modules(self) -> list[str]:
        return sorted(self.files)


def _resolve(target: str, internal: set[str]) -> str | None:
    """Map a dotted import target to the longest internal module prefix, or None
    if it is external. ``from a.b import c`` may import submodule ``a.b.c`` or a
    name inside ``a.b`` — try the longest match first."""
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in internal:
            return cand
    return None


def _abs_from_relative(node: ast.ImportFrom, this_module: str) -> str:
    """Resolve a relative import (``from . import x`` / ``from ..y import z``) to an
    absolute dotted prefix, based on the importing module's package path."""
    pkg_parts = this_module.split(".")[:-1]  # drop the module leaf -> its package
    up = node.level - 1
    base = pkg_parts[:len(pkg_parts) - up] if up <= len(pkg_parts) else []
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base)


def build_import_graph(root: Path | str = ".") -> ImportGraph:
    """Parse every Python file under ``root`` and build the intra-repo import
    graph. Unparseable files are skipped (never crash a whole scan on one bad
    file)."""
    root = Path(root).resolve()
    g = ImportGraph()
    file_of: dict[str, Path] = {}
    for path in _iter_py_files(root):
        mod = _module_name(path)
        if not mod:
            continue
        g.files[mod] = str(path.relative_to(root))
        file_of[mod] = path
    internal = set(g.files)

    for mod, path in file_of.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    hit = _resolve(alias.name, internal)
                    if hit and hit != mod:
                        g.edges[mod].add(hit)
                    elif not hit:
                        g.external[mod].add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                target = (_abs_from_relative(node, mod) if node.level
                          else (node.module or ""))
                if not target:
                    continue
                matched = False
                # An imported name may itself be an internal submodule
                # (``from pkg import mod`` -> edge to ``pkg.mod``, not ``pkg``).
                for alias in node.names:
                    sub = _resolve(f"{target}.{alias.name}", internal)
                    if sub:
                        if sub != mod:
                            g.edges[mod].add(sub)
                        matched = True
                if not matched:
                    # Names live in ``target`` itself (or target is a module):
                    # the dependency is on ``target``.
                    hit = _resolve(target, internal)
                    if hit and hit != mod:
                        g.edges[mod].add(hit)
                        matched = True
                if not matched and not node.level:
                    g.external[mod].add(target.split(".")[0])
    return g


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #

def find_cycles(g: ImportGraph) -> list[list[str]]:
    """Strongly-connected components with more than one module (circular imports),
    plus any self-loop. Tarjan's algorithm, iterative (no recursion-depth limit on
    large repos). Each returned list is one cycle group, modules sorted."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    out: list[list[str]] = []
    nodes = g.modules()

    for start in nodes:
        if start in index:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            succ = sorted(g.edges.get(v, ()))
            if pi < len(succ):
                work[-1] = (v, pi + 1)
                w = succ[pi]
                if w not in index:
                    work.append((w, 0))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
            else:
                if low[v] == index[v]:
                    comp: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1 or v in g.edges.get(v, set()):
                        out.append(sorted(comp))
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[v])
    return sorted(out, key=lambda c: (-len(c), c))


def metrics(g: ImportGraph) -> dict:
    """Per-module fan-in / fan-out plus repo-level roll-ups."""
    fan_out = {m: len(g.edges.get(m, ())) for m in g.modules()}
    fan_in: dict[str, int] = defaultdict(int)
    for deps in g.edges.values():
        for d in deps:
            fan_in[d] += 1
    edge_count = sum(fan_out.values())
    mods = g.modules()
    orphans = [m for m in mods if fan_out.get(m, 0) == 0 and fan_in.get(m, 0) == 0]
    return {
        "modules": len(mods),
        "edges": edge_count,
        "cycles": len(find_cycles(g)),
        "most_depended_on": sorted(
            ((fan_in.get(m, 0), m) for m in mods), reverse=True
        )[:10],
        "most_dependencies": sorted(
            ((fan_out.get(m, 0), m) for m in mods), reverse=True
        )[:10],
        "orphans": orphans,
        "fan_in": dict(fan_in),
        "fan_out": fan_out,
    }


# Framework / tooling signatures: import-name (or manifest marker) -> label.
_STACK_IMPORTS = {
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django", "starlette": "Starlette",
    "pydantic": "Pydantic", "sqlalchemy": "SQLAlchemy", "typer": "Typer", "click": "Click",
    "rich": "Rich", "textual": "Textual", "torch": "PyTorch", "tensorflow": "TensorFlow",
    "numpy": "NumPy", "pandas": "pandas", "anthropic": "Anthropic SDK", "openai": "OpenAI SDK",
    "boto3": "AWS (boto3)", "google": "Google Cloud", "kubernetes": "Kubernetes client",
    "pytest": "pytest", "httpx": "httpx", "requests": "requests", "aiohttp": "aiohttp",
}
_MANIFESTS = {
    "pyproject.toml": "Python (pyproject)", "setup.py": "Python (setup.py)",
    "requirements.txt": "Python (requirements)", "package.json": "Node.js",
    "go.mod": "Go", "Cargo.toml": "Rust", "pom.xml": "Java (Maven)",
    "build.gradle": "Java/Kotlin (Gradle)", "Gemfile": "Ruby", "composer.json": "PHP",
    "Dockerfile": "Docker", "docker-compose.yml": "Docker Compose",
    "go.sum": "Go", "uv.lock": "uv", "poetry.lock": "Poetry",
}


def detect_stack(root: Path | str, g: ImportGraph | None = None) -> dict:
    """Detect languages, frameworks, and infra from manifests + observed imports."""
    root = Path(root).resolve()
    manifests: list[str] = []
    for name, label in _MANIFESTS.items():
        if (root / name).exists() or any(_iter_manifest(root, name)):
            manifests.append(label)
    if any((root / ".github" / "workflows").glob("*.y*ml")) if (root / ".github" / "workflows").exists() else False:
        manifests.append("GitHub Actions")
    if list(root.rglob("*.tf")):
        manifests.append("Terraform")
    frameworks: set[str] = set()
    if g is not None:
        ext = {pkg for pkgs in g.external.values() for pkg in pkgs}
        for imp, label in _STACK_IMPORTS.items():
            if imp in ext:
                frameworks.add(label)
    return {
        "manifests": sorted(set(manifests)),
        "frameworks": sorted(frameworks),
    }


def _iter_manifest(root: Path, name: str):
    # a manifest can live in a subpackage (monorepo); check a shallow rglob
    for p in root.rglob(name):
        if not any(part in _IGNORE_DIRS for part in p.relative_to(root).parts):
            yield p


def top_packages(g: ImportGraph) -> dict[str, int]:
    """Module count per top-level package — the coarse architecture skeleton."""
    counts: dict[str, int] = defaultdict(int)
    for m in g.modules():
        counts[m.split(".")[0]] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def to_json(g: ImportGraph, *, include_metrics: bool = True) -> str:
    payload: dict = {
        "modules": g.modules(),
        "edges": {m: sorted(deps) for m, deps in sorted(g.edges.items()) if deps},
        "files": g.files,
    }
    if include_metrics:
        m = metrics(g)
        m["most_depended_on"] = [[n, mod] for n, mod in m["most_depended_on"]]
        m["most_dependencies"] = [[n, mod] for n, mod in m["most_dependencies"]]
        payload["metrics"] = m
        payload["cycles"] = find_cycles(g)
    return json.dumps(payload, indent=2)


def _sanitize(node: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in node)


def to_mermaid(g: ImportGraph, *, max_nodes: int = 60) -> str:
    """A Mermaid ``graph LR``. On large repos, keep the ``max_nodes`` highest-degree
    modules so the diagram stays readable, and note the truncation."""
    m = metrics(g)
    degree = {mod: m["fan_in"].get(mod, 0) + m["fan_out"].get(mod, 0) for mod in g.modules()}
    keep = set(sorted(degree, key=lambda x: (-degree[x], x))[:max_nodes])
    lines = ["graph LR"]
    truncated = len(g.modules()) - len(keep)
    for mod in sorted(keep):
        lines.append(f'  {_sanitize(mod)}["{mod}"]')
    seen = 0
    for src in sorted(g.edges):
        if src not in keep:
            continue
        for dst in sorted(g.edges[src]):
            if dst in keep:
                lines.append(f"  {_sanitize(src)} --> {_sanitize(dst)}")
                seen += 1
    if truncated > 0:
        lines.append(f'  %% {truncated} lower-degree modules omitted for readability')
    return "\n".join(lines)


def to_text(g: ImportGraph, *, cycles: bool = True) -> str:
    m = metrics(g)
    lines = [
        f"modules: {m['modules']}   edges: {m['edges']}   cycles: {m['cycles']}",
        "",
        "most depended-on:",
    ]
    for n, mod in m["most_depended_on"]:
        if n:
            lines.append(f"  {n:>3}  {mod}")
    if cycles:
        cyc = find_cycles(g)
        lines.append("")
        if cyc:
            lines.append(f"circular imports ({len(cyc)}):")
            for c in cyc[:20]:
                lines.append("  " + " -> ".join(c) + " -> " + c[0])
        else:
            lines.append("circular imports: none")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Reachability & resolution (Ronin v1.1 — impact / changed / why)
# --------------------------------------------------------------------------- #

def reverse_edges(g: ImportGraph) -> dict[str, set[str]]:
    """``rev[m]`` = the set of modules that directly import ``m`` (its dependents)."""
    rev: dict[str, set[str]] = defaultdict(set)
    for src, deps in g.edges.items():
        for d in deps:
            rev[d].add(src)
    return rev


def _reachable(adj: dict[str, set[str]], start: str) -> set[str]:
    """All nodes reachable from ``start`` via ``adj`` (excluding ``start`` itself)."""
    seen: set[str] = set()
    queue = deque(adj.get(start, ()))
    while queue:
        n = queue.popleft()
        if n in seen or n == start:
            continue
        seen.add(n)
        queue.extend(adj.get(n, ()))
    return seen


def transitive_dependents(g: ImportGraph, target: str) -> set[str]:
    """Every module that imports ``target`` directly or transitively — the blast
    radius of a change to ``target``."""
    return _reachable(reverse_edges(g), target)


def transitive_dependencies(g: ImportGraph, target: str) -> set[str]:
    """Every module ``target`` imports directly or transitively."""
    return _reachable(g.edges, target)


def candidates(g: ImportGraph, target: str) -> list[str]:
    """Modules whose leaf name (or full name) matches ``target`` — used to offer a
    'did you mean' when a bare name is ambiguous."""
    stem = Path(target.replace("\\", "/")).stem
    return sorted({m for m in g.modules() if m == target or m.split(".")[-1] == stem})


def resolve_target(g: ImportGraph, target: str) -> str | None:
    """Map a user-supplied module name OR file path to a single module key, or
    ``None`` if it can't be resolved unambiguously."""
    if target in g.files:
        return target
    t = target.replace("\\", "/").lstrip("./")
    for mod, rel in g.files.items():
        rel_n = rel.replace("\\", "/")
        if t == rel_n or rel_n.endswith("/" + t) or rel_n == t:
            return mod
    cands = candidates(g, target)
    return cands[0] if len(cands) == 1 else None
