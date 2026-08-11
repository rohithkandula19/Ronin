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
import importlib.util
import sys
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
        dotted == package or dotted.startswith(f"{package}.") for package in ORCHESTRATOR_PACKAGES
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
    # The tool layer sits on `core` and, in its two network modules, on `safety`:
    # `tools/net.py` fences fetched web content with the canonical wrapper from
    # `safety.injection` and asks `safety.net` whether a URL may be fetched at all, and
    # `tools/fetcher.py` — the HTTP client — asks `safety.net` again for what the
    # hostname *resolves* to, so it can connect to a vetted address instead of to the
    # name. Both edges exist so the rule has one home: the drifting copy is always the
    # one in the layer that merely *uses* it.
    # `safety` is a leaf forbidden from importing `ronin.tools`
    # (see LEAF_FORBIDDEN above), so the edge cannot become a cycle. Listing the
    # tool layer here at all is the point: it was previously unconstrained, so
    # nothing would have noticed a second, less defensible edge appearing.
    ("tools", ("ronin.providers", "ronin.agents", "ronin.mcp", *ABOVE)),
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


def containing_package(path: Path, dotted: str) -> str:
    """The package a relative import in ``path`` is relative *to*.

    For a module it is the parent; for an ``__init__.py`` it is the package itself,
    because ``modules_under`` has already stripped the ``.__init__`` suffix. Getting
    this wrong is not cosmetic: with ``ronin.persistence`` treated as a module inside
    ``ronin``, ``from ..providers import x`` in a package's ``__init__`` resolved to
    ``.providers`` — no ``ronin.`` prefix, so the filter below dropped it and the gate
    saw nothing. A package's ``__init__`` is the most likely file in the tree to import
    across a layer, since re-exporting is its job.
    """
    if path.name == "__init__.py":
        return dotted
    return dotted.rsplit(".", 1)[0] if "." in dotted else dotted


def imported_modules(path: Path, dotted: str) -> set[str]:
    """Absolute ``ronin.*`` module names this file imports, relative ones resolved.

    Covers ``import x``, ``from x import y`` and relative ``from . import y`` — and
    finds them anywhere in the file, including inside a function body.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = containing_package(path, dotted)
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
    forbidden = sorted(name for name in imports if not name.startswith("ronin.core"))
    assert forbidden == [], (
        f"telemetry imports {forbidden}; it may only see ronin.core, because a module "
        "that cannot reach prompts, paths or code cannot transmit them"
    )


def test_persistence_depends_on_core_only() -> None:
    """Stricter than the table above, because the layer's docstring is stricter.

    ``LAYER_RULES`` forbids ``persistence`` the provider, tool, agent, MCP and
    application layers — but would happily allow it ``ronin.context`` or
    ``ronin.safety``, while ``persistence/__init__.py`` says "nothing imported from
    outside ``ronin.core``". A promise a gate does not cover is a promise that holds
    until someone needs a helper, and the one it would cost is the reason the claim is
    there: a transcript codec that reaches sideways can no longer be reasoned about as
    a pure function of ``core`` values, and replaying an old session starts depending
    on what some other layer does today.
    """
    offenders: list[str] = []
    for dotted, path in modules_under("persistence"):
        for imported in imported_modules(path, dotted):
            if imported.startswith("ronin.core") or imported.startswith("ronin.persistence"):
                continue
            offenders.append(f"{dotted} imports {imported}")
    assert offenders == [], offenders


#: The documented order of ``persistence``: each module may import the ones before it
#: and nothing after. ``__init__`` re-exports the whole package and ``demo`` drives it,
#: so both stand outside the line.
#:
#: ``index`` is last because it is the most derived thing in the package: it reads
#: ``transcript`` (for ``SessionMeta``, ``read_events`` and ``list_sessions``) and
#: ``resume`` (for the fold that decides which text is searchable), and nothing reads
#: it. That direction is what keeps the cache out of a cycle with the log it caches —
#: the tempting edge is ``transcript`` importing ``SessionIndex`` to update it, which
#: is why the writer depends on a ``Protocol`` it declares itself instead.
PERSISTENCE_ORDER: tuple[str, ...] = ("codec", "transcript", "resume", "export", "index")


def test_the_persistence_modules_form_a_line() -> None:
    """``codec → transcript → resume → export``, in that direction only.

    The package docstring states this as a fact about the design, and it is the reason
    the layer has no cycles to break: ``codec`` cannot know about files, ``transcript``
    cannot know about replay. Nothing enforced it. The failure it prevents is not
    hypothetical — the tempting edge is ``codec`` importing ``transcript`` for
    ``TranscriptError``, which would put the file format in a cycle with the file.
    """
    rank = {name: index for index, name in enumerate(PERSISTENCE_ORDER)}
    offenders: list[str] = []
    for dotted, path in modules_under("persistence"):
        leaf = dotted.rsplit(".", 1)[-1]
        if leaf not in rank:  # __init__ and demo see the whole package by design
            continue
        for imported in imported_modules(path, dotted):
            other = imported.rsplit(".", 1)[-1]
            if not imported.startswith("ronin.persistence.") or other not in rank:
                continue
            if rank[other] >= rank[leaf]:
                offenders.append(f"{leaf} imports {other}, which is not below it")
    assert offenders == [], offenders


def test_the_persistence_order_lists_every_module_in_the_line() -> None:
    """Guards the table itself: a new module in the package would otherwise be
    unconstrained until someone remembered to rank it, and the only signal would be
    silence."""
    on_disk = {
        dotted.rsplit(".", 1)[-1]
        for dotted, _ in modules_under("persistence")
        if dotted != "ronin.persistence"
    }
    unranked = sorted(on_disk - set(PERSISTENCE_ORDER) - {"demo"})
    assert unranked == [], (
        f"{unranked} are in ronin.persistence but not in PERSISTENCE_ORDER; add them "
        "in dependency order (or to the exemption beside it) so the line stays enforced"
    )


def test_a_relative_import_in_a_package_init_resolves_to_that_package(tmp_path: Path) -> None:
    """`from .codec import X` in `ronin/persistence/__init__.py` is
    `ronin.persistence.codec`, not `ronin.codec`.

    `modules_under` reports an `__init__.py` under the package's own dotted name, so
    treating that name as a *module* and taking its parent walked one level too far up.
    """
    init = tmp_path / "__init__.py"
    init.write_text("from .codec import encode\nfrom . import export\n")
    assert imported_modules(init, "ronin.persistence") == {
        "ronin.persistence.codec",
        "ronin.persistence",
    }


def test_a_cross_layer_relative_import_in_an_init_is_not_invisible(tmp_path: Path) -> None:
    """The violation the old resolution silently dropped.

    `from ..providers.router import Router` in `ronin/tools/__init__.py` resolved to
    `.providers.router`, which fails the `ronin.`-prefix filter and so never reached
    any prohibition. A gate with a blind spot in the file most likely to re-export
    across a layer is worse than no gate: it reports success.
    """
    init = tmp_path / "__init__.py"
    init.write_text("from ..providers.router import Router\n")
    assert imported_modules(init, "ronin.tools") == {"ronin.providers.router"}


def test_the_resolver_still_reads_a_plain_module_relative_to_its_parent(tmp_path: Path) -> None:
    """The control: the fix must not shift resolution for ordinary modules, which is
    where every existing prohibition is enforced."""
    module = tmp_path / "files.py"
    module.write_text("from ..core.types import ToolResult\nfrom .base import Tool\n")
    assert imported_modules(module, "ronin.tools.files") == {
        "ronin.core.types",
        "ronin.tools.base",
    }


def test_ui_depends_on_core_only() -> None:
    """Stricter than the table, because ``ui/__init__`` is stricter: "depends on
    ``ronin.core`` and nothing else — not providers, not tools, not the session".

    ``LEAF_FORBIDDEN`` would allow it ``ronin.context`` or ``ronin.safety``, and the
    first import of either is the end of the property that makes this layer worth its
    shape: every surface consumes an ``AsyncIterator[Event]`` somebody else produced,
    which is why the TUI is testable with no model and no network.
    """
    offenders: list[str] = []
    for dotted, path in modules_under("ui"):
        for imported in imported_modules(path, dotted):
            if imported.startswith("ronin.core") or imported.startswith("ronin.ui"):
                continue
            offenders.append(f"{dotted} imports {imported}")
    assert offenders == [], offenders


#: The four modules ``ui/__init__`` promises "work on a bare install" — no Textual, and
#: on the strength of that promise, nothing third-party at all.
PURE_UI_MODULES: tuple[str, ...] = ("reduce", "render", "commands", "headless")


def test_the_pure_ui_modules_import_nothing_third_party() -> None:
    """ "Zero third-party imports" is what lets the same renderers serve the Textual
    app, the headless runner and an HTML export — and what lets `ronin -p` work when
    the ``tui`` extra was never installed. A single convenience import of ``rich`` here
    would make the bare install fail at startup with an ImportError, which is the worst
    possible first impression of a tool someone just installed.

    Checked against ``sys.stdlib_module_names`` rather than a list kept here. The list
    came first and was wrong on its second reading — it omitted ``pathlib`` — which is
    the whole argument: an enumerated allowlist of the standard library is a thing that
    fails closed on a legitimate import and has to be edited to make a correct change
    pass."""
    offenders: list[str] = []
    for name in PURE_UI_MODULES:
        path = SRC / "ui" / f"{name}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root == "ronin" or root in sys.stdlib_module_names:
                    continue
                offenders.append(f"ui.{name} imports {root}")
    assert offenders == [], offenders


def test_importing_the_ui_package_does_not_import_textual() -> None:
    """The bare-install promise, checked the only way that means anything.

    ``ui/__init__`` imports ``.app`` eagerly, so if ``app.py`` ever grew a module-level
    ``import textual`` the whole package would fail to import without the extra — and
    **CI would not notice**, because the ``tui`` extra *is* installed here. That is the
    shape of a test that cannot fail when it should, so the check has to be about what
    reached ``sys.modules`` rather than about whether the import worked.

    Run in a subprocess: this interpreter has already imported Textual for
    ``tests/ui/test_ui_textual.py``, and once a module is in ``sys.modules`` no
    in-process check can tell whether importing ``ronin.ui`` was what put it there.
    """
    import subprocess

    probe = (
        "import sys\n"
        "import ronin.ui, ronin.ui.reduce, ronin.ui.render, ronin.ui.commands,"
        " ronin.ui.headless\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] == 'textual')\n"
        "print(':'.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"importing ronin.ui pulled in {result.stdout.strip()}"


def test_the_textual_probe_would_notice_an_eager_import() -> None:
    """The control for the test above, which would otherwise pass on a typo.

    Importing ``ronin.ui.app`` and then Textual explicitly must show up, or the probe
    is measuring nothing. Skipped when the extra is absent, since there would be
    nothing to detect.
    """
    import subprocess

    if importlib.util.find_spec("textual") is None:
        pytest.skip("the 'tui' extra is not installed, so there is nothing to detect")
    probe = (
        "import sys\nimport ronin.ui.app\nimport textual\n"
        "print(':'.join(sorted(m for m in sys.modules if m.split('.')[0] == 'textual'))[:20])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() != "", "the probe cannot see Textual even when it is imported"
