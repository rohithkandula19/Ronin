"""Tests for the doctor diagnostics battery (pure, offline)."""
from __future__ import annotations

from ronin_cli import doctor as doc
from ronin_cli.config import RoninConfig


def test_check_python_ok() -> None:
    assert doc.check_python((3, 12)).status == doc.OK
    assert doc.check_python((3, 11)).status == doc.OK


def test_check_python_too_old() -> None:
    d = doc.check_python((3, 9))
    assert d.status == doc.FAIL
    assert d.fix  # has a fix hint


def test_check_config_present_vs_missing() -> None:
    cfg = RoninConfig()
    assert doc.check_config(cfg, "/some/path/config.toml").status == doc.OK
    missing = doc.check_config(cfg, None)
    assert missing.status == doc.WARN
    assert "ronin init" in missing.fix


def test_check_provider_known_and_unknown() -> None:
    assert doc.check_provider(RoninConfig(provider="anthropic")).status == doc.OK
    assert doc.check_provider(RoninConfig(provider="custom")).status == doc.OK
    bad = doc.check_provider(RoninConfig(provider="nope"))
    assert bad.status == doc.FAIL
    assert "nope" in bad.message


def test_check_auth_demo_and_ollama_ok() -> None:
    assert doc.check_auth(RoninConfig(demo_mode=True)).status == doc.OK
    assert doc.check_auth(RoninConfig(provider="ollama")).status == doc.OK


def test_check_auth_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d = doc.check_auth(RoninConfig(provider="anthropic"))
    assert d.status == doc.FAIL
    assert "set-key" in d.fix


def test_check_auth_present_key() -> None:
    cfg = RoninConfig(provider="anthropic", anthropic_api_key="sk-test")
    assert doc.check_auth(cfg).status == doc.OK


def test_check_base_url_custom_requires_url() -> None:
    d = doc.check_base_url(RoninConfig(provider="custom"))
    assert d.status == doc.FAIL
    ok = doc.check_base_url(RoninConfig(provider="custom", base_url="https://x/v1"))
    assert ok.status == doc.OK


def test_check_offline_consistency_flags_cloud_offline() -> None:
    d = doc.check_offline_consistency(RoninConfig(provider="anthropic", offline=True))
    assert d.status == doc.WARN
    # local brain + offline is fine
    assert doc.check_offline_consistency(RoninConfig(provider="ollama", offline=True)).status == doc.OK
    assert doc.check_offline_consistency(RoninConfig(provider="anthropic", offline=False)).status == doc.OK


def test_check_ollama_binary_injected() -> None:
    cfg = RoninConfig(provider="ollama")
    assert doc.check_ollama_binary(cfg, which=lambda _: "/usr/bin/ollama").status == doc.OK
    missing = doc.check_ollama_binary(cfg, which=lambda _: None)
    assert missing.status == doc.FAIL
    # not using ollama → skip
    assert doc.check_ollama_binary(RoninConfig(provider="anthropic"), which=lambda _: None).status == doc.OK


def test_overall_status_and_exit_code() -> None:
    ok = [doc.Diagnosis("a", doc.OK, "")]
    warn = [doc.Diagnosis("a", doc.OK, ""), doc.Diagnosis("b", doc.WARN, "")]
    fail = [doc.Diagnosis("b", doc.WARN, ""), doc.Diagnosis("c", doc.FAIL, "")]
    assert doc.overall_status(ok) == doc.OK
    assert doc.overall_status(warn) == doc.WARN
    assert doc.overall_status(fail) == doc.FAIL
    assert doc.exit_code(ok) == 0
    assert doc.exit_code(warn) == 0
    assert doc.exit_code(fail) == 1


def test_run_checks_returns_full_battery() -> None:
    diagnoses = doc.run_checks(RoninConfig(demo_mode=True), "/cfg/path")
    names = [d.name for d in diagnoses]
    assert names == ["python", "config file", "provider", "auth", "base_url", "ollama", "offline"]
    # demo mode → nothing should fail
    assert doc.overall_status(diagnoses) != doc.FAIL
