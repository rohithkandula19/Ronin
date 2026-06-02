from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app


runner = CliRunner()


def test_save_and_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Save two queries
    r1 = runner.invoke(app, ["save", "mrr", "what is our MRR right now"])
    assert r1.exit_code == 0, r1.stdout
    r2 = runner.invoke(app, ["save", "churn", "which customers churned this month", "--description", "weekly check"])
    assert r2.exit_code == 0

    # List them
    r3 = runner.invoke(app, ["queries"])
    assert r3.exit_code == 0
    assert "mrr" in r3.stdout
    assert "churn" in r3.stdout
    assert "weekly check" in r3.stdout


def test_save_invalid_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["save", "has spaces", "x"])
    assert r.exit_code == 2
    assert "invalid name" in r.stdout.lower()


def test_unsave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["save", "tmp", "delete me"])
    r = runner.invoke(app, ["unsave", "tmp"])
    assert r.exit_code == 0
    r2 = runner.invoke(app, ["unsave", "tmp"])
    assert r2.exit_code == 2


def test_run_missing_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = runner.invoke(app, ["run", "nonexistent"])
    assert r.exit_code == 2
    assert "no saved query" in r.stdout.lower()


def test_queries_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["queries"])
    assert r.exit_code == 0
    assert "no saved queries" in r.stdout.lower()


def test_eval_subcommand_help(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """csk eval should be a subcommand group, replacing the standalone csk-eval."""
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["eval", "--help"])
    assert r.exit_code == 0
    assert "run" in r.stdout
    assert "drift" in r.stdout


def test_run_saved_query_executes_with_fake_provider(tmp_path, monkeypatch) -> None:
    """End-to-end: save a query, then `csk run <name>` invokes the agent."""
    from unittest.mock import patch
    from ronin_agent_patterns import FakeProvider, LLMResponse

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    runner.invoke(app, ["init", "--demo", "-y"])
    runner.invoke(app, ["save", "mrr", "what is our MRR"])

    provider = FakeProvider(responses=[
        LLMResponse(text="MRR is $78/mo from 2 active subscriptions.", stop_reason="end_turn",
                    usage={"input_tokens": 10, "output_tokens": 5}),
    ])
    with patch("ronin_cli.runner.build_provider", return_value=provider):
        r = runner.invoke(app, ["run", "mrr"])
    assert r.exit_code == 0
    assert "$78" in r.stdout
