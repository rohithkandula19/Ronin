from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.config import RoninConfig, load_config
from ronin_cli.main import _key_preview, app

runner = CliRunner()
GOOD_KEY = "gsk_" + "a" * 52       # 56 chars
LONG_KEY = "gsk_" + "a" * 120      # double-pasted-ish


# ---------- api_key alias ----------

def test_api_key_alias_maps_to_openai_key() -> None:
    cfg = RoninConfig(provider="groq", api_key=GOOD_KEY)
    assert cfg.openai_api_key == GOOD_KEY


def test_explicit_openai_key_wins_over_alias() -> None:
    cfg = RoninConfig(openai_api_key="real", api_key="alias")
    assert cfg.openai_api_key == "real"


def test_load_config_reads_api_key_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".ronin").mkdir()
    (tmp_path / ".ronin" / "config.toml").write_text(
        'provider = "groq"\napi_key = "gsk_fromfile"\n', encoding="utf-8")
    cfg = load_config()
    assert cfg.provider == "groq" and cfg.openai_api_key == "gsk_fromfile"


# ---------- _key_preview ----------

def test_key_preview_flags() -> None:
    assert "empty" in _key_preview("")
    assert "looks right" in _key_preview(GOOD_KEY)
    assert "double-pasted" in _key_preview(LONG_KEY)
    assert "short" in _key_preview("gsk_abc")


def test_key_preview_masks_value() -> None:
    out = _key_preview(GOOD_KEY)
    assert GOOD_KEY not in out          # never reveals the full key
    assert "gsk_" in out and "…" in out  # shows safe ends only


# ---------- set-key command ----------

def test_set_key_writes_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = runner.invoke(app, ["set-key", "--provider", "groq"], input=GOOD_KEY + "\n")
    assert r.exit_code == 0, r.stdout
    assert "key saved" in r.stdout
    cfg = load_config()
    assert cfg.provider == "groq" and cfg.openai_api_key == GOOD_KEY
    assert cfg.demo_mode is False


def test_set_key_rejects_double_paste(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = runner.invoke(app, ["set-key", "--provider", "groq"], input=LONG_KEY + "\n")
    assert r.exit_code == 1
    assert "multiple times" in r.stdout


def test_set_key_empty_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["set-key", "--provider", "groq"], input="\n")
    assert r.exit_code == 1
    assert "nothing changed" in r.stdout


def test_set_key_ollama_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["set-key", "--provider", "ollama"])
    assert r.exit_code == 0
    assert "no key" in r.stdout.lower()
