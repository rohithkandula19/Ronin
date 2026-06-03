"""Tests that --fast trims the agent's toolset to the core coding tools."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ronin_agent_patterns import FakeProvider, LLMResponse
from ronin_cli.code_mode import _FAST_TOOLS, run_code_agent
from ronin_cli.config import RoninConfig


def _tool_names_for(config, tmp_path) -> set[str]:
    prov = FakeProvider(responses=[LLMResponse(text="done", stop_reason="end_turn", usage={})])
    with patch("ronin_cli.code_mode.build_provider", return_value=prov):
        run_code_agent(config, "do a thing", root=tmp_path, console=None, yolo=True)
    return set(prov.calls[0]["tool_names"])


def test_fast_mode_keeps_only_core_tools(tmp_path: Path) -> None:
    names = _tool_names_for(RoninConfig(provider="anthropic", fast=True), tmp_path)
    assert names <= _FAST_TOOLS                       # nothing outside the core set
    assert {"read_file", "write_file", "edit_file", "run_command"} <= names


def test_normal_mode_has_more_tools(tmp_path: Path) -> None:
    normal = _tool_names_for(RoninConfig(provider="anthropic"), tmp_path)
    fast = _tool_names_for(RoninConfig(provider="anthropic", fast=True), tmp_path)
    # normal ships the heavy extras (LSP / web / git / …); fast is a strict subset
    assert len(normal) > len(fast)
    assert "web_search" in normal and "web_search" not in fast
    assert "git_status" in normal and "git_status" not in fast
