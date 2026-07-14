"""Tests for plugin scaffolding — name validation, template, write + load."""
from __future__ import annotations

import pytest

from ronin_cli.plugin_scaffold import plugin_template, valid_plugin_name, write_plugin


# The trust gate refuses to IMPORT an untrusted plugin (`.ronin/plugins/` is in the
# repo, so importing is RCE — see test_plugin_trust.py). build_plugin_tools offers
# NO bypass, so trust the fixture like `ronin plugin new` does (it auto-trusts).
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



@pytest.mark.parametrize("name,ok", [
    ("weather", True), ("github_trending", True), ("hn2", True),
    ("Weather", False), ("2cool", False), ("has-dash", False),
    ("class", False), ("", False),
])
def test_valid_plugin_name(name, ok) -> None:
    assert valid_plugin_name(name) is ok


def test_template_is_valid_python_with_register_tools() -> None:
    import ast
    src = plugin_template("weather", "Current weather")
    ast.parse(src)                                   # parses
    assert "def register_tools(" in src
    assert "def weather(" in src
    assert "Current weather" in src


def test_write_plugin_then_load_into_agent(tmp_path) -> None:
    from ronin_cli.plugins import build_plugin_tools
    path = write_plugin("mytool", tmp_path, description="Does a thing")
    assert path.name == "mytool.py"
    tools = _trusted_build(tmp_path)             # the agent's loader
    assert [t.name for t in tools] == ["mytool"]


def test_write_plugin_refuses_overwrite(tmp_path) -> None:
    write_plugin("dup", tmp_path)
    with pytest.raises(FileExistsError):
        write_plugin("dup", tmp_path)
    # overwrite=True succeeds
    write_plugin("dup", tmp_path, overwrite=True)


def test_write_plugin_rejects_bad_name(tmp_path) -> None:
    with pytest.raises(ValueError):
        write_plugin("Bad-Name", tmp_path)
