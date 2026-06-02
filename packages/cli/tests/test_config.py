from __future__ import annotations

import os
from pathlib import Path

import pytest

from ronin_cli.config import (
    RoninConfig,
    find_config_path,
    load_config,
    save_config,
)


def test_demo_mode_lists_all_services() -> None:
    cfg = RoninConfig(demo_mode=True)
    assert set(cfg.configured_services()) == {"stripe", "linear", "slack", "notion", "postgres"}


def test_real_mode_lists_only_configured() -> None:
    cfg = RoninConfig(stripe_api_key="rk_x", linear_api_key="lin_x")
    assert set(cfg.configured_services()) == {"stripe", "linear"}


def test_save_and_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)

    cfg = RoninConfig(stripe_api_key="rk_x", model="claude-sonnet-4-6")
    path = save_config(cfg, scope="project")
    assert path.exists()
    assert find_config_path() == path

    loaded = load_config()
    assert loaded.stripe_api_key == "rk_x"
    assert loaded.model == "claude-sonnet-4-6"


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    save_config(RoninConfig(stripe_api_key="from_file"), scope="project")
    monkeypatch.setenv("STRIPE_API_KEY", "from_env")

    loaded = load_config()
    assert loaded.stripe_api_key == "from_env"


def test_has_anthropic_auth_in_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = RoninConfig(demo_mode=True)
    assert cfg.has_anthropic_auth()


def test_has_anthropic_auth_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert RoninConfig().has_anthropic_auth()
