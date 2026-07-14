from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.plugins import load_plugin_tools, load_plugins


PLUGIN_OK = '''
from ronin_agent_patterns import Tool

def register_tools():
    return [Tool(
        name="echo",
        description="echo back",
        input_schema={"type": "object", "properties": {"v": {"type": "string"}}, "required": ["v"]},
        handler=lambda v: v,
    )]
'''

PLUGIN_NO_FUNC = '''
# missing register_tools()
'''

PLUGIN_WRONG_RETURN = '''
def register_tools():
    return "not a list"
'''

PLUGIN_NON_TOOL = '''
def register_tools():
    return ["not a Tool"]
'''

PLUGIN_RAISES = '''
def register_tools():
    raise RuntimeError("boom")
'''


def _write(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")


def test_loads_valid_plugin(tmp_path: Path) -> None:
    _write(tmp_path, "ok", PLUGIN_OK)
    tools = load_plugin_tools(tmp_path, require_trust=False)
    assert len(tools) == 1
    assert tools[0].name == "echo"
    assert tools[0].handler("hi") == "hi"


def test_missing_register_tools_recorded(tmp_path: Path) -> None:
    _write(tmp_path, "broken", PLUGIN_NO_FUNC)
    results = load_plugins(tmp_path, require_trust=False)
    assert len(results) == 1
    assert results[0].error and "register_tools" in results[0].error


def test_wrong_return_type_recorded(tmp_path: Path) -> None:
    _write(tmp_path, "wrong", PLUGIN_WRONG_RETURN)
    results = load_plugins(tmp_path, require_trust=False)
    assert len(results) == 1
    assert results[0].error and "must return a list" in results[0].error


def test_non_tool_item_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "bad_item", PLUGIN_NON_TOOL)
    results = load_plugins(tmp_path, require_trust=False)
    assert results[0].error and "non-Tool" in results[0].error


def test_plugin_exception_isolated(tmp_path: Path) -> None:
    _write(tmp_path, "boom", PLUGIN_RAISES)
    _write(tmp_path, "ok", PLUGIN_OK)
    tools = load_plugin_tools(tmp_path, require_trust=False)
    # The valid plugin's tool still loads despite the broken one
    assert len(tools) == 1
    assert tools[0].name == "echo"


def test_underscore_files_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "_private", PLUGIN_OK)
    assert load_plugin_tools(tmp_path, require_trust=False) == []


def test_nonexistent_dir_is_empty() -> None:
    assert load_plugin_tools(Path("/nope/no/where"), require_trust=False) == []


def test_example_weather_plugin_loads() -> None:
    """The shipped example plugin should load cleanly."""
    repo = Path(__file__).resolve().parents[3]
    results = load_plugins(repo / "examples" / "plugins", require_trust=False)
    assert any(r.name == "weather" and r.error is None for r in results)
    tools = load_plugin_tools(repo / "examples" / "plugins", require_trust=False)
    assert any(t.name == "weather" for t in tools)


def test_built_plugin_tools_are_sensitive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # build_plugin_tools hardens each tool AND marks it sensitive so the approval
    # gate prompts before a user/library plugin runs (it used to bypass the gate).
    # NOTE: build_plugin_tools deliberately has NO trust bypass — it is the path a
    # hostile repo would use for RCE — so the plugin must be trusted, as a real
    # user's `ronin plugin add/new` would have done. See test_plugin_trust.py.
    from ronin_cli.plugins import build_plugin_tools
    from ronin_cli.plugin_trust import trust
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / "home"))
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "ok.py").write_text(PLUGIN_OK, encoding="utf-8")
    trust(pdir / "ok.py")
    tools = build_plugin_tools(tmp_path)
    assert len(tools) == 1
    assert tools[0].name == "echo"
    assert tools[0].sensitive is True
