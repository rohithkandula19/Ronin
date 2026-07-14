"""Tests for the built-in plugin library."""
from __future__ import annotations

import ast

import pytest

from ronin_cli.plugin_library import LIBRARY, library_rows, resolve


# The trust gate refuses to IMPORT an untrusted plugin file (`.ronin/plugins/` is
# inside the repo, so importing it is RCE — see test_plugin_trust.py).
# build_plugin_tools deliberately offers NO bypass, so these tests trust their
# fixture exactly as `ronin plugin add` does (it auto-trusts on install).
@pytest.fixture(autouse=True)
def _isolated_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / "_trust_home"))


def _trusted_build(root):
    from ronin_cli.plugin_trust import trust
    from ronin_cli.plugins import build_plugin_tools as _b
    pdir = root / ".ronin" / "plugins"
    if pdir.is_dir():
        for f in sorted(pdir.glob("*.py")):
            if not f.name.startswith("_"):
                trust(f)
    return _b(root)



@pytest.mark.parametrize("name", list(LIBRARY))
def test_every_library_source_valid_and_loads(tmp_path, name) -> None:
    from ronin_cli.plugins import build_plugin_tools
    src = LIBRARY[name].source
    ast.parse(src)                                   # valid python
    assert "def register_tools(" in src
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / f"{name}.py").write_text(src, encoding="utf-8")
    tools = _trusted_build(tmp_path)             # loads through the agent loader
    assert len(tools) >= 1


def test_scratchpad_runs_offline(tmp_path, monkeypatch) -> None:
    # the scratchpad plugin needs no network — exercise its handler end to end
    from ronin_cli.plugins import build_plugin_tools
    monkeypatch.chdir(tmp_path)
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "scratchpad.py").write_text(LIBRARY["scratchpad"].source, encoding="utf-8")
    tool = _trusted_build(tmp_path)[0]
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


def test_installed_and_outdated(tmp_path) -> None:
    from ronin_cli.plugin_library import LIBRARY, installed_names, outdated
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    # a current library plugin (up to date)
    (pdir / "roman_numeral.py").write_text(LIBRARY["roman_numeral"].source, encoding="utf-8")
    # a stale library plugin (old content)
    (pdir / "weather.py").write_text("# old version\n", encoding="utf-8")
    # a user plugin (not in library) — must be ignored by outdated()
    (pdir / "mine.py").write_text("# custom\n", encoding="utf-8")
    assert installed_names(tmp_path) == {"roman_numeral", "weather", "mine"}
    assert outdated(tmp_path) == ["weather"]            # only the stale library one
