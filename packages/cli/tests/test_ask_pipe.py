from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ronin_cli.main import app
from ronin_cli.runner import AgentResultRich


def test_ask_folds_in_piped_stdin(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_run_ask(config, question, **kw):
        captured["q"] = question
        return AgentResultRich(success=True, output="ok", iterations=1)

    with patch("ronin_cli.main.run_ask", side_effect=fake_run_ask):
        r = CliRunner().invoke(app, ["ask", "what's the root cause?"], input="Traceback: boom\n")
    assert r.exit_code == 0
    assert "what's the root cause?" in captured["q"]
    assert "Traceback: boom" in captured["q"]      # piped stdin folded in


def test_ask_works_with_only_piped_input(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.chdir(tmp_path)
    captured = {}
    with patch("ronin_cli.main.run_ask",
               side_effect=lambda c, q, **k: (captured.__setitem__("q", q),
                                              AgentResultRich(success=True, output="ok", iterations=1))[1]):
        r = CliRunner().invoke(app, ["ask"], input="summarize this log\n")
    assert r.exit_code == 0
    assert "summarize this log" in captured["q"]
