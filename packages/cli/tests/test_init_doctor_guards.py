from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ro_claude_kit_cli import main as cli_main
from ro_claude_kit_cli.config import CSKConfig
from ro_claude_kit_cli.main import _provider_live_check, app, load_config

runner = CliRunner()


# ---------- init guard: reject "yes" etc. as a model ----------

def test_init_rejects_yes_as_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # answers: provider=groq, model="yes" (bogus), key=gsk_x, then 5 empty service prompts
    r = runner.invoke(app, ["init"], input="groq\nyes\ngsk_testkey\n\n\n\n\n\n")
    assert r.exit_code == 0, r.stdout
    assert "isn't a model name" in r.stdout

    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.provider == "groq"
    assert cfg.model == "llama-3.3-70b-versatile"   # fell back to default, NOT "yes"


def test_init_keeps_valid_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["init"], input="groq\nllama-3.1-8b-instant\ngsk_testkey\n\n\n\n\n\n")
    assert r.exit_code == 0, r.stdout
    monkeypatch.chdir(tmp_path)
    assert load_config().model == "llama-3.1-8b-instant"


# ---------- doctor --check live ping ----------

class _HTTPError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(str(code))
        self.code = code


def test_live_check_invalid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = CSKConfig(provider="groq", model="llama-3.3-70b-versatile", openai_api_key="bad")
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 401, "no", {}, None)):
        msg = _provider_live_check(cfg)
    assert "invalid key" in msg and "401" in msg


def test_live_check_model_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = CSKConfig(provider="groq", model="yes", openai_api_key="gsk_ok")

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"data":[{"id":"llama-3.3-70b-versatile"},{"id":"llama-3.1-8b-instant"}]}'

    with patch("urllib.request.urlopen", return_value=_Resp()):
        msg = _provider_live_check(cfg)
    assert "model 'yes' not found" in msg


def test_live_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = CSKConfig(provider="groq", model="llama-3.3-70b-versatile", openai_api_key="gsk_ok")

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"data":[{"id":"llama-3.3-70b-versatile"}]}'

    with patch("urllib.request.urlopen", return_value=_Resp()):
        msg = _provider_live_check(cfg)
    assert "ok" in msg.lower() and "valid" in msg


def test_live_check_no_key() -> None:
    cfg = CSKConfig(provider="groq", model="x")  # no key
    assert "no key" in _provider_live_check(cfg).lower()


def test_doctor_check_flag_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    runner.invoke(app, ["init", "--demo", "-y"])
    with patch.object(cli_main, "_provider_live_check", return_value="[green]ok[/green]"):
        r = runner.invoke(app, ["doctor", "--check"])
    assert r.exit_code == 0
    assert "live check" in r.stdout
