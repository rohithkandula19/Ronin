"""Tests for build_plugin_tools — root-aware agent plugin loading."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.plugins import build_plugin_tools

_PLUGIN = '''
from ronin_agent_patterns import Tool

def _handler(city: str) -> dict:
    return {"city": city, "temp": 72}

def register_tools():
    return [Tool(name="fake_weather", description="Fake weather",
                 input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
                 handler=_handler)]
'''


# The trust gate (plugin_trust) refuses to IMPORT a plugin file that was not
# explicitly trusted: `.ronin/plugins/` lives in the repo, so importing it is RCE
# (see test_plugin_trust.py). build_plugin_tools deliberately offers NO bypass, so
# these tests trust their fixture exactly as a real user's `ronin plugin
# new/add/from-api` does (those auto-trust on write).
@pytest.fixture(autouse=True)
def _isolated_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / "_trust_home"))


def _trusted_build(root):
    """Trust every plugin in <root>/.ronin/plugins, then load via the agent path."""
    from ronin_cli.plugin_trust import trust
    from ronin_cli.plugins import build_plugin_tools as _b
    pdir = root / ".ronin" / "plugins"
    if pdir.is_dir():
        for f in sorted(pdir.glob("*.py")):
            if not f.name.startswith("_"):
                trust(f)
    return _b(root)


def test_build_plugin_tools_loads_from_root(tmp_path: Path) -> None:
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "weather.py").write_text(_PLUGIN, encoding="utf-8")
    tools = _trusted_build(tmp_path)
    assert [t.name for t in tools] == ["fake_weather"]
    assert tools[0].handler(city="Austin") == {"city": "Austin", "temp": 72}


def test_build_plugin_tools_empty_when_no_dir(tmp_path: Path) -> None:
    assert _trusted_build(tmp_path) == []


def test_build_plugin_tools_skips_broken(tmp_path: Path) -> None:
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "bad.py").write_text("this is not valid python !!!", encoding="utf-8")
    # broken plugin is skipped, not fatal
    assert _trusted_build(tmp_path) == []


def test_handler_exceptions_become_clean_errors(tmp_path) -> None:
    # a plugin whose handler raises must not crash — it returns {"error": ...}
    from ronin_cli.plugins import build_plugin_tools
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "boom.py").write_text(
        "from ronin_agent_patterns import Tool\n"
        "def _h(x=0):\n    raise ValueError('kaboom')\n"
        "def register_tools():\n"
        "    return [Tool(name='boomtool', description='d',\n"
        "                 input_schema={'type':'object','properties':{}}, handler=_h)]\n",
        encoding="utf-8")
    tool = _trusted_build(tmp_path)[0]
    out = tool.handler()                       # must NOT raise
    assert isinstance(out, dict) and "error" in out
    assert "kaboom" in out["error"] and "boomtool" in out["error"]


def test_harden_passes_through_success(tmp_path) -> None:
    from ronin_cli.plugins import build_plugin_tools
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "ok.py").write_text(
        "from ronin_agent_patterns import Tool\n"
        "def _h(n=2):\n    return {'doubled': n*2}\n"
        "def register_tools():\n"
        "    return [Tool(name='ok', description='d',\n"
        "                 input_schema={'type':'object','properties':{}}, handler=_h)]\n",
        encoding="utf-8")
    tool = _trusted_build(tmp_path)[0]
    assert tool.handler(n=5) == {"doubled": 10}
