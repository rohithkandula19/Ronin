"""``obs`` is a leaf: every module under it imports only stdlib and ronin.core.types.

This is the property the parent relies on to place ``obs`` at the bottom of the layer
graph. It is checked here with ``ast`` rather than by importing, so a lazy import buried
inside a function — the place a boundary violation would actually hide — cannot slip a
provider, tool, or the loop in later.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

OBS = Path(__file__).resolve().parents[2] / "src" / "ronin" / "obs"

#: The only ``ronin.*`` import the layer is allowed, besides its own submodules.
ALLOWED_RONIN = frozenset({"ronin.core.types"})


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, i.e. within ronin.obs
                found.add(f"ronin.obs.{node.module}" if node.module else "ronin.obs")
            elif node.module:
                found.add(node.module)
    return found


def _modules() -> list[Path]:
    return [p for p in sorted(OBS.rglob("*.py")) if "__pycache__" not in p.parts]


def test_the_package_has_modules_to_check() -> None:
    assert _modules(), "no modules found under src/ronin/obs"


def test_obs_imports_only_stdlib_and_core_types() -> None:
    offenders: list[str] = []
    for path in _modules():
        for name in sorted(_imports(path)):
            root = name.split(".")[0]
            if root != "ronin":
                if root not in sys.stdlib_module_names:
                    offenders.append(f"{path.name} imports non-stdlib {name!r}")
                continue
            if name.startswith("ronin.obs"):
                continue  # its own submodules
            if name not in ALLOWED_RONIN:
                offenders.append(f"{path.name} imports {name!r} (only ronin.core.types is allowed)")
    assert offenders == [], offenders
