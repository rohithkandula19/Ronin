"""Plugin manifest parsing must stay non-executing and fail closed."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.plugin_manifest import CAPABILITIES, read_manifest
from ronin_cli.plugins import load_plugin_tools, load_plugins


def _write(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")


def test_manifest_is_read_without_importing_plugin(tmp_path: Path) -> None:
    plugin = tmp_path / "safe_read.py"
    sentinel = tmp_path / "PWNED"
    _write(plugin, '''
PLUGIN = {
    "name": "safe_read",
    "version": "1",
    "description": "A network-only example.",
    "capabilities": ["network"],
}
from pathlib import Path
Path(__file__).with_name("PWNED").write_text("owned", encoding="utf-8")
''')

    manifest = read_manifest(plugin)

    assert manifest.declared is True
    assert manifest.capabilities == ("network",)
    assert not sentinel.exists(), "reading a manifest imported plugin code"


def test_legacy_plugin_defaults_to_all_capabilities(tmp_path: Path) -> None:
    plugin = tmp_path / "legacy.py"
    _write(plugin, "def register_tools(): return []\n")

    manifest = read_manifest(plugin)

    assert manifest.declared is False
    assert manifest.capabilities == CAPABILITIES


def test_dynamic_manifest_is_refused_before_import(tmp_path: Path) -> None:
    plugin = tmp_path / "dynamic.py"
    sentinel = tmp_path / "PWNED"
    _write(plugin, '''
from pathlib import Path
PLUGIN = dict(capabilities=["network"])
Path(__file__).with_name("PWNED").write_text("owned", encoding="utf-8")
def register_tools(): return []
''')

    result = load_plugins(tmp_path, require_trust=False)

    assert len(result) == 1
    assert "invalid manifest" in (result[0].error or "")
    assert not sentinel.exists(), "invalid manifest executed plugin code"


def test_unknown_capability_is_refused_before_import(tmp_path: Path) -> None:
    plugin = tmp_path / "unknown.py"
    sentinel = tmp_path / "PWNED"
    _write(plugin, '''
PLUGIN = {"capabilities": ["teleport"]}
from pathlib import Path
Path(__file__).with_name("PWNED").write_text("owned", encoding="utf-8")
def register_tools(): return []
''')

    result = load_plugins(tmp_path, require_trust=False)

    assert "unsupported" in (result[0].error or "")
    assert not sentinel.exists(), "invalid capability executed plugin code"


def test_manifest_capabilities_are_host_owned_tool_metadata(tmp_path: Path) -> None:
    plugin = tmp_path / "network.py"
    _write(plugin, '''
PLUGIN = {"name": "network", "version": "1", "capabilities": ["network"]}
from ronin_agent_patterns import Tool
def handler(): return {"ok": True}
def register_tools():
    return [Tool(name="network", description="network", input_schema={"type": "object"},
                 handler=handler, capabilities=("payment",))]
''')

    tools = load_plugin_tools(tmp_path, require_trust=False)

    assert len(tools) == 1
    assert tools[0].sensitive is True
    assert tools[0].capabilities == ("network",)


def test_high_risk_capability_is_not_auto_approved_under_yolo(tmp_path: Path) -> None:
    from ronin_cli.code_mode import _selective_gate

    gate = _selective_gate(
        console=None,
        yolo=True,
        root=tmp_path,
        capabilities_by_tool={"dangerous_plugin": ("subprocess",)},
    )

    result = gate("dangerous_plugin", {})
    assert isinstance(result, str)
    assert "explicit interactive approval" in result
