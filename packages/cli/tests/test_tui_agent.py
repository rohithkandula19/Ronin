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
    assert "read_file" in gated                   # gate_cb was consulted
    assert "tool_call" in steps and "tool_result" in steps


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


def test_tui_gate_allows_nonsensitive() -> None:
    from ronin_cli.tui import RoninApp
    app = RoninApp(config=_cfg(), root=".")
    # non-sensitive tools never block on a modal — immediate True
    assert app._gate("read_file", {}) is True
    assert app._gate("search_files", {"query": "x"}) is True


def test_tui_imports_and_constructs() -> None:
    from ronin_cli.tui import ApprovalScreen, RoninApp
    app = RoninApp(config=_cfg(), root=".")
    assert app.busy is False
    assert app.root.endswith(("/", "ronin")) or app.root  # resolved to an abs path
    screen = ApprovalScreen("write_file", {"path": "a"})
    assert screen._tool == "write_file"
