"""Guard: docs/INTEGRATIONS.md must list every plugin + reflect the catalog size,
so the docs can't silently drift as the library grows."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.mcp_catalog import CATALOG
from ronin_cli.plugin_library import LIBRARY


def _doc() -> str:
    # walk up from this test file to the repo root, find docs/INTEGRATIONS.md
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "docs" / "INTEGRATIONS.md"
        if cand.is_file():
            return cand.read_text(encoding="utf-8")
    pytest.skip("docs/INTEGRATIONS.md not found")


def test_every_plugin_is_documented() -> None:
    doc = _doc()
    missing = [name for name in LIBRARY if f"`{name}`" not in doc]
    assert not missing, f"undocumented plugins: {missing}"


def test_doc_states_correct_counts() -> None:
    doc = _doc()
    assert f"{len(LIBRARY)} built-ins" in doc or f"{len(LIBRARY)} plugins" in doc
    assert f"{len(CATALOG)} servers" in doc
