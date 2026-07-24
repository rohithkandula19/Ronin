"""CLI-4: `ronin ask --json` emits a single machine-readable JSON object on
stdout (and nothing else there), so scripts/CI can consume agent answers."""
from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from ronin_cli.main import app
from ronin_cli.runner import AgentResultRich

runner = CliRunner()


def test_ask_json_emits_single_parseable_object(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    fake = AgentResultRich(
        success=True, output="port 8080 is open", iterations=1,
        trace=[{"kind": "tool_call", "content": "run_command(ss -tlnp)"}],
        usage={"input_tokens": 12, "output_tokens": 5},
    )
    with patch("ronin_cli.main.run_ask", return_value=fake), \
         patch("ronin_cli.main.load_config") as lc:
        lc.return_value.has_provider_auth.return_value = True
        lc.return_value.provider = "anthropic"
        res = runner.invoke(app, ["ask", "--json", "which ports are open?"])
    assert res.exit_code == 0
    obj = json.loads(res.output)              # stdout is EXACTLY one JSON object
    assert obj["result"] == "port 8080 is open"
    assert obj["success"] is True
    assert obj["tool_calls"] == ["run_command(ss -tlnp)"]
    assert obj["usage"]["input_tokens"] == 12
    assert "duration" in obj and "files_changed" in obj and "cost" in obj


def test_ask_json_exit_code_reflects_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    fake = AgentResultRich(success=False, output="", iterations=0, error="boom")
    with patch("ronin_cli.main.run_ask", return_value=fake), \
         patch("ronin_cli.main.load_config") as lc:
        lc.return_value.has_provider_auth.return_value = True
        lc.return_value.provider = "anthropic"
        res = runner.invoke(app, ["ask", "--json", "q"])
    assert res.exit_code == 1
    assert json.loads(res.output)["success"] is False
