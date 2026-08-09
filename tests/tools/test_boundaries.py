"""The dependency graph, enforced by CI instead of by reading.

``docs/ARCHITECTURE.md`` §3 states the package boundaries as prohibitions, and until
now they were documentation — the honest status section said so. This is the test
that makes them a gate.

It parses imports with ``ast`` rather than importing the modules, so a violation is
caught even if the offending import is inside a function (which is exactly where one
would hide: a lazy import to "avoid the cycle" is how a boundary quietly dissolves).
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "ronin"

#: The one module allowed to know about every layer. It exists to introduce them.
ORCHESTRATORS = frozenset({"ronin.session", "ronin.session_demo"})


def modules_under(package: str) -> Iterator[tuple[str, Path]]:
    """Every module in ``src/ronin/<package>``, as ``(dotted_name, path)``."""
    root = SRC / package if package else SRC
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(SRC.parent)
        dotted = ".".join(relative.with_suffix("").parts)
        yield dotted.removesuffix(".__init__"), path


def imported_modules(path: Path, dotted: str) -> set[str]:
    """Absolute ``ronin.*`` module names this file imports, relative ones resolved.

    Covers ``import x``, ``from x import y`` and relative ``from . import y`` — and
    finds them anywhere in the file, including inside a function body.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = dotted.rsplit(".", 1)[0] if "." in dotted else dotted
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # `from ..core.types import X` inside `ronin.tools.files` → walk up
                # `level - 1` packages from the containing package.
                parts = package.split(".")
                base = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                prefix = ".".join(base)
                found.add(f"{prefix}.{node.module}" if node.module else prefix)
            elif node.module:
                found.add(node.module)
    return {name for name in found if name == "ronin" or name.startswith("ronin.")}


def violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    """Every ``module → forbidden import`` pair, excluding the orchestrators."""
    found: list[str] = []
    for dotted, path in modules_under(package):
        if dotted in ORCHESTRATORS:
            continue
        for imported in imported_modules(path, dotted):
            for prefix in forbidden:
                if imported == prefix or imported.startswith(f"{prefix}."):
                    found.append(f"{dotted} imports {imported}")
    return found


# --------------------------------------------------------------------------- #
# The prohibitions
# --------------------------------------------------------------------------- #


def test_the_tool_layer_knows_nothing_about_providers_or_the_loop() -> None:
    """A tool that knew which model was calling it would special-case for it."""
    assert violations("tools", ("ronin.providers", "ronin.core.loop")) == []


def test_the_provider_layer_knows_nothing_about_tools() -> None:
    """An adapter that imports tools ends up executing them, which is how the
    provider layer quietly becomes a second agent loop."""
    assert violations("providers", ("ronin.tools",)) == []


def test_the_core_contract_knows_nothing_about_providers_or_tools() -> None:
    """The loop takes both as injected protocols; importing either is the cycle."""
    assert violations("core", ("ronin.providers", "ronin.tools")) == []


def test_the_loop_imports_only_types_and_protocols() -> None:
    """`run_turn`'s "zero provider, zero UI knowledge" claim, as a test."""
    path = SRC / "core" / "loop.py"
    imports = imported_modules(path, "ronin.core.loop")
    assert imports <= {"ronin.core.types", "ronin.core.protocols"}


def test_only_the_orchestrator_imports_all_three_layers() -> None:
    """If a second module starts doing the introductions, the seat has moved."""
    multi: list[str] = []
    for dotted, path in modules_under(""):
        imports = imported_modules(path, dotted)
        layers = {
            "core" if any(i.startswith("ronin.core") for i in imports) else "",
            "providers" if any(i.startswith("ronin.providers") for i in imports) else "",
            "tools" if any(i.startswith("ronin.tools") for i in imports) else "",
        } - {""}
        if len(layers) == 3:
            multi.append(dotted)
    assert set(multi) <= ORCHESTRATORS, f"unexpected cross-layer module(s): {multi}"


def test_the_orchestrator_does_import_all_three() -> None:
    """The inverse: if it stopped, the wiring moved somewhere it should not be."""
    imports = imported_modules(SRC / "session.py", "ronin.session")
    assert any(name.startswith("ronin.core") for name in imports)
    assert any(name.startswith("ronin.providers") for name in imports)
    assert any(name.startswith("ronin.tools") for name in imports)


# --------------------------------------------------------------------------- #
# The parser itself, since the tests above are only as good as it is
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import ronin.providers", {"ronin.providers"}),
        ("from ronin.providers import X", {"ronin.providers"}),
        ("from ronin.providers.router import Router", {"ronin.providers.router"}),
        ("def f():\n    from ronin.providers import X", {"ronin.providers"}),
        ("import os\nimport json", set()),
    ],
)
def test_the_import_parser_finds_absolute_imports(
    tmp_path: Path, source: str, expected: set[str]
) -> None:
    path = tmp_path / "m.py"
    path.write_text(source)
    assert imported_modules(path, "ronin.tools.m") == expected


def test_the_import_parser_resolves_relative_imports(tmp_path: Path) -> None:
    """`from ..core.types import X` in `ronin.tools.files` is `ronin.core.types`."""
    path = tmp_path / "m.py"
    path.write_text("from ..core.types import ToolResult\nfrom .base import Tool\n")
    assert imported_modules(path, "ronin.tools.files") == {
        "ronin.core.types",
        "ronin.tools.base",
    }


def test_a_lazy_import_inside_a_function_is_still_caught(tmp_path: Path) -> None:
    """The place a boundary violation would actually hide."""
    path = tmp_path / "m.py"
    path.write_text(
        "def build():\n"
        "    # deferred to 'avoid the cycle'\n"
        "    from ronin.providers.router import Router\n"
        "    return Router\n"
    )
    assert imported_modules(path, "ronin.tools.m") == {"ronin.providers.router"}
