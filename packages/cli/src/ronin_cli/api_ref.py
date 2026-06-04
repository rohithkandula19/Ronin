"""Generate a Markdown API reference — `ronin api`.

Walks a package, extracts every public function/class (with its real signature and
docstring summary via ``ast``), and renders a clean Markdown reference — no Sphinx,
no config, no doc-comments required. The extraction and rendering are pure and
unit-tested; the command just writes the file.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin", ".mypy_cache"}


@dataclass
class ApiSymbol:
    name: str
    kind: str           # "function" | "class"
    signature: str
    summary: str        # first line of the docstring (may be "")
    line: int
    methods: list[str] = field(default_factory=list)   # public method signatures (classes)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Reconstruct ``name(args) -> return`` from the ast. Pure."""
    try:
        args = ast.unparse(node.args)
    except Exception:  # noqa: BLE001
        args = ""
    sig = f"{node.name}({args})"
    if node.returns is not None:
        try:
            sig += f" -> {ast.unparse(node.returns)}"
        except Exception:  # noqa: BLE001
            pass
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    return prefix + sig


def _summary(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    return doc.strip().split("\n", 1)[0].strip()


def extract_api(source: str, rel: str) -> list[ApiSymbol]:
    """Public top-level functions/classes with signatures + doc summaries. Pure."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[ApiSymbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            out.append(ApiSymbol(node.name, "function", _signature(node),
                                 _summary(node), node.lineno))
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            methods = [
                _signature(b) for b in node.body
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not b.name.startswith("_")
            ]
            out.append(ApiSymbol(node.name, "class", f"class {node.name}",
                                 _summary(node), node.lineno, methods=methods))
    return out


def render_markdown(modules: list[tuple[str, list[ApiSymbol]]], *, title: str = "API Reference") -> str:
    """Render extracted API into Markdown. Pure."""
    lines = [f"# {title}", ""]
    total = sum(len(syms) for _, syms in modules)
    lines.append(f"_{total} public symbol(s) across {len(modules)} module(s)._")
    lines.append("")
    for mod, syms in modules:
        if not syms:
            continue
        lines.append(f"## `{mod}`")
        lines.append("")
        for s in syms:
            lines.append(f"### `{s.signature}`")
            if s.summary:
                lines.append("")
                lines.append(s.summary)
            if s.kind == "class" and s.methods:
                lines.append("")
                for m in s.methods:
                    lines.append(f"- `{m}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_api(root: Path | str) -> list[tuple[str, list[ApiSymbol]]]:
    """Collect the public API of every module under ``root``, sorted by path.
    Modules with no public symbols are dropped. Pure given the tree."""
    rp = Path(root).resolve()
    out: list[tuple[str, list[ApiSymbol]]] = []
    for p in sorted(rp.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        if p.name.startswith("test_") or p.name == "conftest.py":
            continue
        rel = str(p.relative_to(rp))
        try:
            syms = extract_api(p.read_text(encoding="utf-8", errors="ignore"), rel)
        except OSError:
            continue
        if syms:
            out.append((rel, syms))
    return out
