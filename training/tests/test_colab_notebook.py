"""Every command in the Colab notebook must be one that actually runs.

A notebook is documentation that claims to be executable, and the failure mode is
specific: ``python -m ronin_training.adapter.threeway --help`` **exits 0 and prints
nothing**, because that module is a library with no ``main()``. A cell like that looks
fine in review, looks fine when run, and does nothing — which is worse than an error,
since the reader concludes the step succeeded.

So the module paths and script flags the notebook invokes are checked here against the
real code. No GPU, no network, no ML stack: this asserts that the *entry points exist
and accept the flags*, which is the part that rots.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "training" / "colab" / "train_free.ipynb"

#: A shell line in a notebook cell: `!python -m thing --flag`.
SHELL = re.compile(r"^\s*!\s*(.+)$")

#: `python -m <module>` inside such a line, with the module captured.
MODULE = re.compile(r"python\s+-m\s+([A-Za-z_][\w.]*)")


@pytest.fixture(scope="module")
def notebook() -> dict[str, object]:
    return dict(json.loads(NOTEBOOK.read_text(encoding="utf-8")))


def shell_lines(notebook: dict[str, object]) -> list[str]:
    """Every ``!``-prefixed line across every code cell, comments excluded.

    Commented-out lines are skipped deliberately: the notebook uses them for the
    alternative lane (a hosted model, the v1 recipe) and those are illustrations, not
    claims that the command runs here.
    """
    found: list[str] = []
    cells = notebook.get("cells", [])
    assert isinstance(cells, list)
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        for raw in "".join(cell.get("source", [])).splitlines():
            match = SHELL.match(raw)
            if match:
                found.append(match.group(1).strip())
    return found


def test_the_notebook_is_valid_json_with_cells(notebook: dict[str, object]) -> None:
    assert notebook.get("nbformat") == 4
    cells = notebook.get("cells", [])
    assert isinstance(cells, list) and cells


def test_every_python_m_module_is_importable(notebook: dict[str, object]) -> None:
    """A module path that does not resolve is a cell that cannot run."""
    modules = {
        match.group(1)
        for line in shell_lines(notebook)
        if (match := MODULE.search(line))
    }
    assert modules, "the notebook invokes no modules; the extraction is broken"
    for name in sorted(modules):
        assert importlib.util.find_spec(name) is not None, f"{name} is not importable"


def test_every_invoked_module_actually_has_an_entry_point(
    notebook: dict[str, object],
) -> None:
    """The bug this file exists for.

    ``python -m pkg.mod`` runs ``pkg/mod.py`` as ``__main__``. A module with no
    ``if __name__ == "__main__"`` guard therefore executes its imports and exits 0,
    silently. The notebook previously invoked ``ronin_training.adapter.threeway`` that
    way — a library module — and the cell did nothing while appearing to succeed.
    """
    modules = {
        match.group(1)
        for line in shell_lines(notebook)
        if (match := MODULE.search(line))
    }
    for name in sorted(modules):
        if not name.startswith("ronin"):
            continue
        spec = importlib.util.find_spec(name)
        assert spec is not None and spec.origin, name
        origin = Path(spec.origin)
        # A package resolves to its __init__.py; `-m` then runs its __main__.py.
        source = origin.parent / "__main__.py" if origin.name == "__init__.py" else origin
        assert source.is_file(), f"python -m {name} has no {source.name}"
        text = source.read_text(encoding="utf-8")
        assert "__main__" in text, (
            f"python -m {name} resolves to {source}, which has no "
            '`if __name__ == "__main__"` guard — the command would exit 0 doing nothing'
        )


def test_the_adapter_package_dispatches_the_subcommands_the_notebook_uses(
    notebook: dict[str, object],
) -> None:
    """`validate` and `preflight` are real dispatch keys, not hopeful strings."""
    from ronin_training.adapter.__main__ import COMMANDS

    used = {
        parts[3]
        for line in shell_lines(notebook)
        if (parts := line.split()) and len(parts) > 3
        and parts[1:3] == ["-m", "ronin_training.adapter"]
        and not parts[3].startswith("-")
        and not parts[3].startswith("$")
    }
    assert used, "the notebook calls no adapter subcommand"
    for name in sorted(used):
        assert name in COMMANDS, f"`{name}` is not a dispatch key: {sorted(COMMANDS)}"


def test_the_older_bare_config_form_still_works() -> None:
    """Docs and training/README.md use it; dispatch must not have broken it."""
    from ronin_training.adapter.__main__ import main

    assert main([str(ROOT / "training" / "config" / "adapter_sft.yaml")]) == 0


def test_every_train_cuda_flag_the_notebook_passes_is_declared(
    notebook: dict[str, object],
) -> None:
    """A stale flag in a notebook is an error 40 minutes into a rented GPU hour."""
    script = ROOT / "training" / "cuda" / "train_cuda.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    declared = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert "--config" in declared, "the resolver flag must be declared"

    used: set[str] = set()
    for line in shell_lines(notebook):
        if "train_cuda.py" not in line:
            continue
        used.update(token for token in line.split() if token.startswith("--"))
    assert used, "the notebook does not invoke the trainer"
    unknown = sorted(used - declared)
    assert unknown == [], f"the notebook passes flags train_cuda.py does not declare: {unknown}"


def test_the_notebook_states_which_recipe_each_lane_trains(
    notebook: dict[str, object],
) -> None:
    """Two recipes differing 20x on learning rate must not be silently interchangeable.

    Somebody reading only Cell 3 should not be able to run the wrong experiment without
    having been told there are two.
    """
    text = "".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]  # type: ignore[union-attr]
        if cell.get("cell_type") == "code"
    )
    assert "2e-4" in text and "1e-5" in text, "the conflicting rates must both be named"
    assert "--check" in text, "the notebook must show how to see which recipe would run"


def test_the_notebook_forbids_placeholder_numbers(notebook: dict[str, object]) -> None:
    """The standing rule, written where somebody filling in a table will read it."""
    text = "".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]  # type: ignore[union-attr]
    )
    assert "NO NUMBER BELONGS IN THIS NOTEBOOK UNTIL A RUN PRODUCES IT" in text
