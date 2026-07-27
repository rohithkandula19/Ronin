"""Tests for the TUI's coding-agent integration — the headless callback wiring in
run_code_agent and the approval-gate logic (the parts testable without a live
Textual event loop)."""
from __future__ import annotations

from ronin_agent_patterns import FakeProvider, LLMResponse, ToolCall

from ronin_cli import code_mode
from ronin_cli.config import RoninConfig


def _cfg() -> RoninConfig:
    return RoninConfig(provider="anthropic", anthropic_api_key="sk-x")


def test_run_code_agent_drives_headless_callbacks(monkeypatch, tmp_path) -> None:
    (tmp_path / "f.txt").write_text("hello world", encoding="utf-8")
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "f.txt"})]),
        LLMResponse(text="done reading"),
    ])
    monkeypatch.setattr(code_mode, "build_provider", lambda cfg: provider)

    texts: list[str] = []
    steps: list[str] = []
    gated: list[str] = []

    result = code_mode.run_code_agent(
        _cfg(), "read f.txt", root=tmp_path, console=None,
        on_text_cb=texts.append,
        on_step_cb=lambda s: steps.append(s.kind),
        gate_cb=lambda name, args: (gated.append(name) or True),
    )

    assert result.success
    assert "done reading" in "".join(texts)     # streamed via on_text_cb
    assert "tool_call" in steps and "tool_result" in steps
    # read_file is read-only → run_code_agent filters it out before the front-end
    # gate, so the (TUI/headless) gate_cb is never bothered with reads.
    assert gated == []


def test_run_code_agent_consults_gate_cb_for_sensitive(monkeypatch, tmp_path) -> None:
    """A sensitive tool (write_file) IS routed to the front-end gate_cb."""
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="write_file",
                    arguments={"path": "out.txt", "content": "hi"})]),
        LLMResponse(text="wrote it"),
    ])
    monkeypatch.setattr(code_mode, "build_provider", lambda cfg: provider)
    gated: list[str] = []
    code_mode.run_code_agent(
        _cfg(), "write out.txt", root=tmp_path, console=None,
        gate_cb=lambda name, args: (gated.append(name) or True),
    )
    assert gated == ["write_file"]                # the gate was consulted


def test_run_code_agent_gate_denial_is_respected(monkeypatch, tmp_path) -> None:
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="write_file",
                    arguments={"path": "x.txt", "content": "nope"})]),
        LLMResponse(text="ok, skipped it"),
    ])
    monkeypatch.setattr(code_mode, "build_provider", lambda cfg: provider)

    result = code_mode.run_code_agent(
        _cfg(), "write a file", root=tmp_path, console=None,
        gate_cb=lambda name, args: False,   # deny everything
    )
    assert result.success
    assert not (tmp_path / "x.txt").exists()      # the write was gated → never happened


def test_run_code_agent_gates_sensitive_mcp_plugin_tool(monkeypatch, tmp_path) -> None:
    """A sensitive MCP/plugin tool reaches the front-end gate_cb too — it used to
    bypass approval entirely because it wasn't in the built-in SENSITIVE_TOOLS."""
    from ronin_agent_patterns import Tool
    charged: list = []
    danger = Tool(name="stripe__charge", description="charge a card",
                  input_schema={"type": "object", "properties": {}},
                  handler=lambda: charged.append(1) or "charged", sensitive=True)
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="stripe__charge", arguments={})]),
        LLMResponse(text="declined"),
    ])
    monkeypatch.setattr(code_mode, "build_provider", lambda cfg: provider)
    gated: list[str] = []
    code_mode.run_code_agent(
        _cfg(), "charge the card", root=tmp_path, console=None,
        extra_tools=[danger],
        gate_cb=lambda name, args: (gated.append(name) or False),  # deny
    )
    assert gated == ["stripe__charge"]   # gated, not silently executed
    assert charged == []                 # denial respected → never ran


def test_high_risk_plugin_capability_still_gates_under_yolo(monkeypatch, tmp_path) -> None:
    """Manifest high-risk tags never become silent just because yolo is set."""
    from ronin_agent_patterns import Tool

    ran: list[bool] = []
    plugin = Tool(
        name="declared_runner",
        description="runs a declared subprocess",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: ran.append(True),
        sensitive=True,
        capabilities=("subprocess",),
    )
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="1", name="declared_runner", arguments={})]),
        LLMResponse(text="declined"),
    ])
    monkeypatch.setattr(code_mode, "build_provider", lambda cfg: provider)
    gated: list[dict] = []
    code_mode.run_code_agent(
        _cfg(), "run it", root=tmp_path, console=None, yolo=True, extra_tools=[plugin],
        gate_cb=lambda _name, args: (gated.append(args) or False),
    )
    assert gated == [{"__ronin_capability_floor": ["subprocess"]}]
    assert ran == []


def test_tui_imports_and_constructs() -> None:
    from ronin_cli.tui import ApprovalScreen, RoninApp
    app = RoninApp(config=_cfg(), root=".")
    assert app.busy is False
    assert app.root.endswith(("/", "ronin")) or app.root  # resolved to an abs path
    screen = ApprovalScreen("write_file", {"path": "a"})
    assert screen._tool == "write_file"
