"""Tests for the built-in plugin library."""
from __future__ import annotations

import ast

import pytest

from ronin_cli.plugin_library import LIBRARY, library_rows, resolve


@pytest.mark.parametrize("name", list(LIBRARY))
def test_every_library_source_valid_and_loads(tmp_path, name) -> None:
    from ronin_cli.plugins import build_plugin_tools
    src = LIBRARY[name].source
    ast.parse(src)                                   # valid python
    assert "def register_tools(" in src
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / f"{name}.py").write_text(src, encoding="utf-8")
    tools = build_plugin_tools(tmp_path)             # loads through the agent loader
    assert len(tools) >= 1


def test_scratchpad_runs_offline(tmp_path, monkeypatch) -> None:
    # the scratchpad plugin needs no network — exercise its handler end to end
    from ronin_cli.plugins import build_plugin_tools
    monkeypatch.chdir(tmp_path)
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "scratchpad.py").write_text(LIBRARY["scratchpad"].source, encoding="utf-8")
    tool = build_plugin_tools(tmp_path)[0]
    assert tool.handler(action="add", text="ship it")["added"] == "ship it"
    assert "ship it" in tool.handler(action="list")["notes"]
    assert tool.handler(action="clear")["cleared"] is True
    assert tool.handler(action="list")["notes"] == []


def test_resolve_aliases() -> None:
    assert resolve("hn").name == "hackernews"
    assert resolve("notes").name == "scratchpad"
    assert resolve("WEATHER").name == "weather"
    assert resolve("nope") is None


def test_library_rows_cover_all() -> None:
    rows = library_rows()
    assert {r[0] for r in rows} == set(LIBRARY)


def test_search_by_name_blurb_alias() -> None:
    from ronin_cli.plugin_library import search
    names = lambda q: {p.name for p in search(q)}
    assert "crypto_price" in names("crypto")        # name/alias
    assert "github_user" in names("github")         # name substring
    assert "synonyms" in names("thesaurus")         # alias match
    assert names("") == set(__import__("ronin_cli.plugin_library", fromlist=["LIBRARY"]).LIBRARY)
    assert search("zzzznope") == []
