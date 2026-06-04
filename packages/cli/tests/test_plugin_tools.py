"""Tests for build_plugin_tools — root-aware agent plugin loading."""
from __future__ import annotations

from pathlib import Path

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


def test_build_plugin_tools_loads_from_root(tmp_path: Path) -> None:
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "weather.py").write_text(_PLUGIN, encoding="utf-8")
    tools = build_plugin_tools(tmp_path)
    assert [t.name for t in tools] == ["fake_weather"]
    assert tools[0].handler(city="Austin") == {"city": "Austin", "temp": 72}


def test_build_plugin_tools_empty_when_no_dir(tmp_path: Path) -> None:
    assert build_plugin_tools(tmp_path) == []


def test_build_plugin_tools_skips_broken(tmp_path: Path) -> None:
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "bad.py").write_text("this is not valid python !!!", encoding="utf-8")
    # broken plugin is skipped, not fatal
    assert build_plugin_tools(tmp_path) == []
