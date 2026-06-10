from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from ronin_agent_patterns import FakeProvider, LLMResponse
from ronin_cli.code_mode import run_code_agent
from ronin_cli.config import RoninConfig
from ronin_cli.main import app
from ronin_cli.project_memory import (
    load_project_memory,
    memory_system_block,
    write_memory_template,
)

runner = CliRunner()


def test_load_finds_ronin_md(tmp_path: Path) -> None:
    (tmp_path / "RONIN.md").write_text("use tabs not spaces", encoding="utf-8")
    found = load_project_memory(tmp_path)
    assert found is not None
    assert found[0] == "RONIN.md"
    assert "tabs" in found[1]


def test_ronin_md_wins_over_claude_md(tmp_path: Path) -> None:
    (tmp_path / "RONIN.md").write_text("ronin notes", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude notes", encoding="utf-8")
    name, text = load_project_memory(tmp_path)  # type: ignore[misc]
    assert name == "RONIN.md" and "ronin" in text


def test_claude_md_used_when_no_ronin(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("claude notes", encoding="utf-8")
    name, _ = load_project_memory(tmp_path)  # type: ignore[misc]
    assert name == "CLAUDE.md"


def test_missing_returns_none(tmp_path: Path) -> None:
    assert load_project_memory(tmp_path) is None


def test_memory_block_contains_contents(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("always run ruff", encoding="utf-8")
    block, name = memory_system_block(tmp_path)  # type: ignore[misc]
    assert name == "AGENTS.md"
    assert "always run ruff" in block and "AGENTS.md" in block


def test_write_template_creates_and_does_not_clobber(tmp_path: Path) -> None:
    p = write_memory_template(tmp_path)
    assert p.exists() and "RONIN.md" in p.name
    p.write_text("custom", encoding="utf-8")
    write_memory_template(tmp_path)  # must not overwrite
    assert p.read_text(encoding="utf-8") == "custom"


def _simple_provider() -> FakeProvider:
    return FakeProvider(responses=[
        LLMResponse(text="Understood.", stop_reason="end_turn",
                    usage={"input_tokens": 5, "output_tokens": 2}),
    ])


def test_code_agent_injects_memory_into_system(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "RONIN.md").write_text("NEVER use print(); use logging.", encoding="utf-8")
    config = RoninConfig(provider="anthropic")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    provider = _simple_provider()

    with patch("ronin_cli.code_mode.build_provider", return_value=provider):
        result = run_code_agent(config, "say ok", root=tmp_path, console=console, yolo=True)

    assert result.success
    # the memory contents made it into the system prompt sent to the provider
    assert "NEVER use print()" in provider.calls[0]["system"]
    # and the load was announced to the user
    assert "loaded project memory" in buf.getvalue()


def test_code_init_scaffolds_memory(tmp_path: Path) -> None:
    r = runner.invoke(app, ["code", "--init", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert (tmp_path / "RONIN.md").is_file()
    assert "project memory" in r.stdout
