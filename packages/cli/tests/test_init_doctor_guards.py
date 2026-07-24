from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ronin_cli import main as cli_main
from ronin_cli.config import PROVIDER_PRESETS, RoninConfig
from ronin_cli.main import _LiveCheck, _provider_live_check, app, load_config

runner = CliRunner()


# ---------- init guard: reject "yes" etc. as a model ----------

def test_init_rejects_yes_as_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Isolate the user-global config dir so a real ~/.config/ronin/config.toml on
    # the dev machine doesn't trigger the interactive "overwrite?" guard.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("ronin_cli.config.USER_DIR", tmp_path / "userconfig")
    # answers: provider=groq, model="yes" (bogus), key=gsk_x, then 5 empty service prompts
    r = runner.invoke(app, ["init"], input="groq\nyes\ngsk_testkey\n\n\n\n\n\n")
    assert r.exit_code == 0, r.stdout
    assert "isn't a model name" in r.stdout

    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.provider == "groq"
    # Fell back to the provider's preset default, NOT the bogus "yes".
    assert cfg.model == PROVIDER_PRESETS["groq"]["model"]


def test_init_keeps_valid_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Isolate the user-global config dir (see test_init_rejects_yes_as_model).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("ronin_cli.config.USER_DIR", tmp_path / "userconfig")
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
    cfg = RoninConfig(provider="groq", model="llama-3.3-70b-versatile", openai_api_key="bad")
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 401, "no", {}, None)):
        res = _provider_live_check(cfg)
    # An auth failure short-circuits before the completion probe.
    assert not res.ok
    assert "invalid key" in res.status and "401" in res.status
    assert "set-key" in res.remedy.lower() or "key" in res.remedy.lower()


def test_live_check_model_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = RoninConfig(provider="groq", model="yes", openai_api_key="gsk_ok")

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"data":[{"id":"llama-3.3-70b-versatile"},{"id":"llama-3.1-8b-instant"}]}'

    # Model 'yes' isn't in the live list → short-circuits before the probe and
    # points the user at `ronin models`.
    with patch("urllib.request.urlopen", return_value=_Resp()):
        res = _provider_live_check(cfg)
    assert not res.ok
    assert "'yes'" in res.status and "not served" in res.status
    assert "ronin models" in res.remedy


def test_live_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = RoninConfig(provider="groq", model="llama-3.3-70b-versatile", openai_api_key="gsk_ok")

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"data":[{"id":"llama-3.3-70b-versatile"}]}'

    # Model is in the live list AND the end-to-end completion probe succeeds.
    with patch("urllib.request.urlopen", return_value=_Resp()), \
         patch.object(cli_main, "_tiny_completion_probe", return_value=(True, None)):
        res = _provider_live_check(cfg)
    assert res.ok
    assert "ok" in res.status.lower() and "valid" in res.status


def test_live_check_invalid_model_via_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the models list doesn't expose the id (empty list) but the real probe
    404s, doctor still flags the model and points at `ronin models`."""
    cfg = RoninConfig(provider="groq", model="bogus-model", openai_api_key="gsk_ok")

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"data":[]}'  # provider didn't list models

    class _Err(Exception):
        def __init__(self) -> None:
            super().__init__("model not found")
            self.response = type("R", (), {"status_code": 404})()

    with patch("urllib.request.urlopen", return_value=_Resp()), \
         patch.object(cli_main, "_tiny_completion_probe", return_value=(False, _Err())):
        res = _provider_live_check(cfg)
    assert not res.ok
    assert "404" in res.status and "invalid" in res.status
    assert "ronin models" in res.remedy


def test_live_check_no_key() -> None:
    cfg = RoninConfig(provider="groq", model="x")  # no key
    res = _provider_live_check(cfg)
    assert not res.ok
    assert "no key" in res.status.lower()
    assert res.remedy  # tells the user how to set one


def test_doctor_check_flag_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    runner.invoke(app, ["init", "--demo", "-y"])
    fake = _LiveCheck(status="[green]ok[/green]", ok=True)
    with patch.object(cli_main, "_provider_live_check", return_value=fake):
        r = runner.invoke(app, ["util", "doctor", "--check"])
    assert r.exit_code == 0
    assert "live check" in r.stdout


def test_doctor_check_exits_nonzero_when_live_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --check is a verification gate: a failed/unverifiable live check (bad or
    # missing key, unreachable model) must exit non-zero, not silently return 0.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    runner.invoke(app, ["init", "--demo", "-y"])
    fail = _LiveCheck(status="no key configured", remedy="Fix: set a key", ok=False)
    with patch.object(cli_main, "_provider_live_check", return_value=fail):
        r = runner.invoke(app, ["util", "doctor", "--check"])
    assert r.exit_code == 1, r.stdout          # the P2 fix
    assert "live check" in r.stdout            # still shows the honest reason row


def test_doctor_without_check_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Static doctor (no --check) only reports key presence — always exit 0.
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--demo", "-y"])
    r = runner.invoke(app, ["util", "doctor"])
    assert r.exit_code == 0, r.stdout
