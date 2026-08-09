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

#: The modules allowed to know about every layer. They exist to introduce them:
#: ``session`` is the orchestrator seat, ``cli`` is the application on top of it,
#: and the two demos show the whole stack running.
#:
#: ``cli`` is stated as a *package* rather than enumerated module by module. The
#: enumerated form was worse than useless: a new ``cli`` module was unconstrained
#: until someone remembered to add it, and the only signal that they had not was a
#: failure in an unrelated test. The rule is "the application layer may import
#: anything", so that is what is written down.
ORCHESTRATOR_MODULES = frozenset({"ronin.session", "ronin.session_demo"})
#: ``evals`` drives the assembled agent through ``cli.sdk.Agent``, so it sits *above*
#: cli rather than beside it. Listing it as an orchestrator package is the honest
#: description: a harness that measures the application is part of the application.
ORCHESTRATOR_PACKAGES = ("ronin.cli", "ronin.evals")


def is_orchestrator(dotted: str) -> bool:
    """Whether ``dotted`` is allowed to import across every layer."""
    return dotted in ORCHESTRATOR_MODULES or any(
        dotted == package or dotted.startswith(f"{package}.")
        for package in ORCHESTRATOR_PACKAGES
    )

#: The layers above ``core`` and their prohibitions, as a table — this *is* the
#: dependency graph from ``docs/ARCHITECTURE.md`` §3, in executable form.
#:
#: The leaf layers take a model, a subprocess runner or a summarizer as an
#: injected callable rather than importing one, which is what lets each of them
#: be tested with no provider, no network and no shell. An import here would not
#: just be untidy: it would make that testability impossible to rely on.
#: Every leaf layer forbids the same set, so name it once. Repeating a six-element
#: tuple five times is how one row quietly ends up missing an entry.
LEAF_FORBIDDEN: tuple[str, ...] = (
    "ronin.providers",
    "ronin.tools",
    "ronin.agents",
    "ronin.mcp",
    "ronin.cli",
    "ronin.evals",
)

#: The two application-layer packages. Nothing below them may import them.
ABOVE: tuple[str, ...] = ("ronin.cli", "ronin.evals")

LAYER_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("context", LEAF_FORBIDDEN),
    ("safety", LEAF_FORBIDDEN),
    ("verify", LEAF_FORBIDDEN),
    ("persistence", LEAF_FORBIDDEN),
    ("ui", LEAF_FORBIDDEN),
    # These two sit above the tool layer: they produce Tools, so they may import
    # it. They still may not know which model is calling them.
    ("agents", ("ronin.providers", *ABOVE)),
    ("mcp", ("ronin.providers", *ABOVE)),
)


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
        if is_orchestrator(dotted):
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
    assert violations("tools", ("ronin.providers", "ronin.core.loop", *ABOVE)) == []


def test_the_provider_layer_knows_nothing_about_tools() -> None:
    """An adapter that imports tools ends up executing them, which is how the
    provider layer quietly becomes a second agent loop."""
    assert violations("providers", ("ronin.tools", *ABOVE)) == []


def test_the_core_contract_knows_nothing_about_providers_or_tools() -> None:
    """The loop takes both as injected protocols; importing either is the cycle."""
    assert violations("core", ("ronin.providers", "ronin.tools", *ABOVE)) == []


@pytest.mark.parametrize(("package", "forbidden"), LAYER_RULES, ids=[r[0] for r in LAYER_RULES])
def test_each_layer_imports_only_what_the_graph_allows(
    package: str, forbidden: tuple[str, ...]
) -> None:
    """§3's table, enforced. A layer absent from disk vacuously passes."""
    if not (SRC / package).is_dir():
        pytest.skip(f"{package}/ not present")
    assert violations(package, forbidden) == []


def test_the_layer_rules_cover_every_package_that_exists() -> None:
    """The table cannot silently stop covering a package someone adds.

    Without this, a new ``src/ronin/foo/`` would be unconstrained and the suite
    would still be green — the failure mode of every allowlist ever written.
    """
    on_disk = {
        path.name
        for path in SRC.iterdir()
        if path.is_dir() and path.name != "__pycache__" and (path / "__init__.py").exists()
    }
    known = {rule[0] for rule in LAYER_RULES} | {"core", "providers", "tools", "cli", "evals"}
    assert on_disk <= known, (
        f"package(s) with no entry in LAYER_RULES: {sorted(on_disk - known)} — "
        "add them to the table with their prohibitions, or the graph is a lie"
    )


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
    unexpected = [dotted for dotted in multi if not is_orchestrator(dotted)]
    assert unexpected == [], f"unexpected cross-layer module(s): {unexpected}"


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


def test_telemetry_depends_on_core_only() -> None:
    """The most privacy-sensitive module in the tree, kept unable to reach anything.

    ``telemetry`` is a single module, so the directory-driven ``LAYER_RULES`` above
    never sees it — which is exactly how a module acquires an import nobody notices.
    It must not reach the tool layer (paths, commands, file contents), the provider
    layer (prompts), or ``cli``; if it cannot see them, it cannot send them.
    """
    path = SRC / "telemetry.py"
    if not path.exists():
        pytest.skip("telemetry.py not present")
    imports = imported_modules(path, "ronin.telemetry")
    forbidden = sorted(
        name
        for name in imports
        if not name.startswith("ronin.core")
    )
    assert forbidden == [], (
        f"telemetry imports {forbidden}; it may only see ronin.core, because a module "
        "that cannot reach prompts, paths or code cannot transmit them"
    )
