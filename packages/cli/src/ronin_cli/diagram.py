"""Architecture diagrams — `ronin diagram`.

Two complementary views of a repo's structure, both deterministic (no model):

* **Package-relative import graph** (``import_graph`` / ``detect_package``) — for a
  single Python package, which module imports which *sibling*. Tight + precise;
  used by ``ronin diagram --pkg`` and ``ronin onboard``.
* **Repo-wide import graph** (``scan_imports`` / ``build_graph`` / ``generate``) —
  walks the whole tree, parses Python ``import`` / ``from x import`` *and* JS/TS
  ``import … from`` statements, keeps only edges between modules that actually
  live in the repo, and renders a grouped Mermaid graph. This is what
  ``ronin diagram`` emits by default.

Every transform here is pure and unit-tested; only ``scan_imports`` / ``generate``
touch the filesystem.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# ----- package-relative (sibling) imports — `from .x import …` --------------
_REL_IMPORT_RE = re.compile(r"^\s*from\s+\.(\w+)\s+import", re.MULTILINE)
_REL_IMPORT2_RE = re.compile(r"^\s*from\s+\.\s+import\s+(.+)$", re.MULTILINE)

# ----- repo-wide import statements (Python + JS/TS) -------------------------
# Python: `import a.b.c`, `import a, b`, `from a.b import x`
_PY_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)", re.MULTILINE)
_PY_FROM_RE = re.compile(r"^\s*from\s+([\w.]+)\s+import", re.MULTILINE)
# JS/TS: `import x from 'a/b'`, `import {x} from "a"`, `export … from 'a'`,
# bare `import 'a'`, and dynamic `import('a')` / `require('a')`.
_JS_FROM_RE = re.compile(r"""(?:import|export)\b[^'"]*?\bfrom\s+['"]([^'"]+)['"]""")
_JS_BARE_RE = re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.MULTILINE)
_JS_REQUIRE_RE = re.compile(r"""(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)""")

_PY_EXT = {".py"}
_JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_CODE_EXT = _PY_EXT | _JS_EXT

_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".turbo", ".idea",
    ".tox", ".gradle", "target", "vendor", ".cache", "coverage",
}
_MAX_FILES = 4000
_MAX_BYTES = 500_000

# Mermaid node ids must be alphanumeric/underscore; everything else → "_".
_ID_SANITIZE_RE = re.compile(r"\W")


# ---------------------------------------------------------------------------
# Pure: package-relative (sibling) import extraction
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Pure: grouping + graph building + Mermaid rendering
# ---------------------------------------------------------------------------
def package_of(path: str) -> str:
    """Top-level package / directory a module path belongs to (its grouping key).

    ``"pkg/sub/mod"`` → ``"pkg"``; ``"pkg.sub.mod"`` → ``"pkg"``;
    a bare ``"mod"`` → ``"mod"``. Slashes and dots are both treated as
    separators, so it works on filesystem paths *and* dotted module names.
    Pure."""
    p = (path or "").strip().strip("/")
    # normalise both separators to "/" then take the first segment
    p = p.replace("\\", "/").replace(".", "/")
    head = p.split("/", 1)[0]
    return head or path


def _sanitize_id(name: str) -> str:
    """A Mermaid-safe node id: dotted/dashed/slashed names → underscores, and a
    leading digit gets an ``n`` prefix (ids may not start with a number). Pure."""
    sid = _ID_SANITIZE_RE.sub("_", name or "")
    if not sid:
        sid = "n"
    if sid[0].isdigit():
        sid = "n" + sid
    return sid


def build_graph(imports: dict[str, set[str]]) -> dict:
    """Turn ``{module: {imported modules}}`` into ``{"nodes": [...], "edges": [(a, b)]}``.

    * **Internal-only**: an edge ``a → b`` is kept only when ``b`` is itself a key
      in ``imports`` (i.e. a module that lives in this repo). Imports of stdlib /
      third-party packages are dropped.
    * **De-duped**: ``nodes`` is each module once (sorted); ``edges`` is each
      ``(a, b)`` pair once (sorted), with self-edges removed.

    Pure."""
    known = set(imports)
    nodes = sorted(known)
    edges: set[tuple[str, str]] = set()
    for src, deps in imports.items():
        for dep in deps:
            if dep in known and dep != src:
                edges.add((src, dep))
    return {"nodes": nodes, "edges": sorted(edges)}


def _coerce_graph(graph: dict) -> dict:
    """Accept either the new ``{"nodes", "edges"}`` shape or a raw
    ``{module: {deps}}`` import-graph, and return the former. Lets ``to_mermaid``
    serve both the repo-wide pipeline and the legacy package pipeline."""
    if "nodes" in graph and "edges" in graph:
        return graph
    return build_graph(graph)  # type: ignore[arg-type]


def to_mermaid(graph: dict, *, direction: str = "LR", title: str = "") -> str:
    """Render a graph as a Mermaid ``graph <direction>``. Pure.

    ``graph`` may be either ``{"nodes": [...], "edges": [(a, b)]}`` (the
    ``build_graph`` output) or a raw ``{module: {deps}}`` mapping — both are
    accepted. ``direction`` is the Mermaid layout (``LR`` | ``TD`` | ``RL`` |
    ``BT``); ``title`` (optional) is emitted as a ``%%`` comment.

    Node ids are sanitized (dotted/dashed names → underscores) while the human
    label keeps the original name, so ``a.b`` renders as ``a_b["a.b"]``.
    """
    g = _coerce_graph(graph)
    direction = (direction or "LR").upper()
    if direction not in {"LR", "TD", "RL", "BT", "TB"}:
        direction = "LR"

    lines = ["```mermaid"]
    if title:
        lines.append(f"%% {title}")
    lines.append(f"graph {direction}")

    nodes: list[str] = list(g.get("nodes", []))
    edges = [tuple(e) for e in g.get("edges", [])]

    # Declare every node so isolated ones still show, and so labels survive
    # sanitization. Only emit an explicit decl when the id differs from the
    # label *or* the node has no edges (keeps simple graphs terse).
    connected = {a for a, _ in edges} | {b for _, b in edges}
    for name in nodes:
        sid = _sanitize_id(name)
        if sid != name:
            lines.append(f'  {sid}["{name}"]')
        elif name not in connected:
            lines.append(f"  {name}")

    for a, b in edges:
        lines.append(f"  {_sanitize_id(a)} --> {_sanitize_id(b)}")

    lines.append("```")
    return "\n".join(lines)


def stats(graph: dict[str, set[str]]) -> dict:
    """Structural facts: most-depended-on modules + leaves. Pure.

    Accepts a raw ``{module: {deps}}`` import-graph."""
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


# ---------------------------------------------------------------------------
# Pure: per-file import parsing for the repo-wide scan
# ---------------------------------------------------------------------------
def _py_imports(text: str) -> set[str]:
    """Dotted module targets of every Python import in ``text``. Pure.
    ``import a.b, c`` → {"a.b", "c"}; ``from a.b import x`` → {"a.b"}."""
    out: set[str] = set()
    for m in _PY_IMPORT_RE.finditer(text):
        for part in m.group(1).split(","):
            part = part.split(" as ")[0].strip()
            if part:
                out.add(part)
    for m in _PY_FROM_RE.finditer(text):
        out.add(m.group(1).strip())
    return out


def _js_imports(text: str) -> set[str]:
    """Import specifiers of every JS/TS import in ``text``. Pure.
    Includes ``from '…'`` , bare ``import '…'`` , and ``require('…')``."""
    out: set[str] = set()
    for rx in (_JS_FROM_RE, _JS_BARE_RE, _JS_REQUIRE_RE):
        for m in rx.finditer(text):
            spec = m.group(1).strip()
            if spec:
                out.add(spec)
    return out


def _walk_code(root: Path):
    """Yield code files under ``root``, pruning vendored / build / hidden dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix.lower() in _CODE_EXT:
                yield Path(dirpath) / fn


def _module_key(rel: Path) -> str:
    """Repo-relative path → a stable module key (POSIX path without extension).
    ``a/b/c.py`` → ``a/b/c``; ``a/b/index.ts`` → ``a/b`` (JS index collapses)."""
    rel = rel.with_suffix("")
    if rel.name == "index" and rel.parent != Path("."):
        rel = rel.parent
    return rel.as_posix()


def _resolve_py(target: str, by_dotted: dict[str, str]) -> str | None:
    """Resolve a dotted Python import target to an internal module key, trying
    progressively shorter prefixes (``a.b.c`` → ``a.b`` → ``a``)."""
    parts = target.split(".")
    while parts:
        hit = by_dotted.get(".".join(parts))
        if hit:
            return hit
        parts.pop()
    return None


def _resolve_js(spec: str, src_key: str, keys: set[str]) -> str | None:
    """Resolve a JS/TS import specifier to an internal module key. Only relative
    specs (``./`` , ``../``) can point at repo files; bare ones are packages."""
    if not spec.startswith("."):
        return None
    base = Path(src_key).parent
    target = Path(os.path.normpath(base / spec)).as_posix()
    # A direct hit, or a directory whose index file collapsed to the dir key
    # (see `_module_key`) — both surface as the same `target` key.
    return target if target in keys else None


def scan_imports(root: Path | str) -> dict[str, set[str]]:
    """Walk ``root`` and build a repo-wide ``{module: {internal modules it imports}}``.

    Parses both Python (``import`` / ``from x import``) and JS/TS
    (``import … from`` / bare imports / ``require``). Modules are keyed by their
    repo-relative, extension-less POSIX path; only edges that land on another
    scanned module are kept (third-party / stdlib imports are dropped). Impure
    (reads the filesystem)."""
    rp = Path(root).resolve()
    files: list[tuple[str, Path]] = []
    for path in _walk_code(rp):
        try:
            rel = path.relative_to(rp)
        except ValueError:
            continue
        files.append((_module_key(rel), path))
        if len(files) >= _MAX_FILES:
            break

    keys = {k for k, _ in files}
    # dotted lookup for Python: "a/b/c" → "a.b.c", and also the bare stem.
    by_dotted: dict[str, str] = {}
    for k, _ in files:
        by_dotted[k.replace("/", ".")] = k
        by_dotted.setdefault(k.rsplit("/", 1)[-1], k)

    graph: dict[str, set[str]] = {k: set() for k in keys}
    for key, path in files:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > _MAX_BYTES:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        deps: set[str] = set()
        if path.suffix.lower() in _PY_EXT:
            for tgt in _py_imports(text):
                hit = _resolve_py(tgt, by_dotted)
                if hit:
                    deps.add(hit)
        else:
            for spec in _js_imports(text):
                hit = _resolve_js(spec, key, keys)
                if hit:
                    deps.add(hit)
        deps.discard(key)
        graph[key] = deps
    return graph


# ---------------------------------------------------------------------------
# Pure: detect the heart-of-the-codebase Python package
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Impure: end-to-end generate (scan → graph → mermaid → optional write)
# ---------------------------------------------------------------------------
def generate(root: Path | str, *, out: str | None = None, direction: str = "LR",
             title: str = "") -> str:
    """Scan ``root``, build the internal import graph, render it as Mermaid, and
    (if ``out`` is given) write the diagram to that file. Returns the Mermaid
    text. Impure."""
    imports = scan_imports(root)
    graph = build_graph(imports)
    if not title:
        title = f"architecture: {Path(root).resolve().name}"
    mermaid = to_mermaid(graph, direction=direction, title=title)
    if out:
        body = mermaid + "\n"
        # Wrap in a tiny Markdown doc when the target looks like Markdown.
        if str(out).lower().endswith((".md", ".markdown")):
            body = f"# {title}\n\n{mermaid}\n"
        Path(out).write_text(body, encoding="utf-8")
    return mermaid
