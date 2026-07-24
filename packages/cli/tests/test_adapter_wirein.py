"""The pre-staged wire-in for ronin-code-1.5b: the moment a real, gate-passing
adapter exists, `RONIN_ADAPTER=<dir>` + provider `local` runs it with ZERO code
changes. These tests prove the wiring with a stub adapter (guard-complete dir),
offline — swapping in the real weights is a pure env-var change.

Fail-closed and grammar-default coverage live in test_embedded_provider.py
(validate_adapter_dir refusals; RONIN_GRAMMAR default-on with a fake backend).
"""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ronin_cli.config import RoninConfig
from ronin_cli.main import app


def _stub_adapter(tmp_path: Path) -> Path:
    stub = tmp_path / "adapters"
    stub.mkdir()
    (stub / "adapter_config.json").write_text("{}")
    (stub / "adapters.safetensors").write_bytes(b"\x00")
    return stub


def test_env_adapter_reaches_the_embedded_provider(tmp_path, monkeypatch):
    # RONIN_ADAPTER → runner → EmbeddedProvider.adapter_path, no code changes.
    from ronin_cli.embedded_provider import EmbeddedProvider
    from ronin_cli.runner import build_single_provider

    stub = _stub_adapter(tmp_path)
    monkeypatch.setenv("RONIN_ADAPTER", str(stub))
    provider = build_single_provider(RoninConfig(provider="local"))
    assert isinstance(provider, EmbeddedProvider)      # engine import is lazy
    assert provider.adapter_path == str(stub)


def test_util_models_smoke_shows_the_valid_adapter(tmp_path, monkeypatch):
    stub = _stub_adapter(tmp_path)
    monkeypatch.setenv("RONIN_ADAPTER", str(stub))
    r = CliRunner().invoke(app, ["util", "models"])
    assert r.exit_code == 0
    assert "ronin-code-1.5b" in r.output and "valid" in r.output


def test_util_models_smoke_reports_fail_closed_on_a_broken_adapter(tmp_path, monkeypatch):
    broken = tmp_path / "half-baked"
    broken.mkdir()
    (broken / "adapter_config.json").write_text("{}")   # weights missing
    monkeypatch.setenv("RONIN_ADAPTER", str(broken))
    r = CliRunner().invoke(app, ["util", "models"])
    assert r.exit_code == 0                             # informational command
    assert "REFUSE" in r.output                         # …but the truth is loud


def test_local_provider_row_needs_no_key(monkeypatch):
    monkeypatch.delenv("RONIN_ADAPTER", raising=False)
    r = CliRunner().invoke(app, ["util", "models"])
    assert r.exit_code == 0
    assert "local" in r.output and "no (local)" in r.output
    # and the wire-in instruction is printed when no adapter is set
    assert "RONIN_ADAPTER" in r.output
