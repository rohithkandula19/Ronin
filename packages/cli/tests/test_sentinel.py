"""Tests for Sentinel mode — confidence parsing + abstention signal."""
from __future__ import annotations

from ronin_cli.sentinel import (
    confidence_badge,
    is_low,
    parse_confidence,
    strip_confidence,
)


def test_parse_each_level() -> None:
    assert parse_confidence("done.\nCONFIDENCE: high") == "high"
    assert parse_confidence("hmm\nCONFIDENCE: Medium") == "medium"
    assert parse_confidence("not sure\nCONFIDENCE: low") == "low"
    assert parse_confidence("no marker here") is None


def test_last_marker_wins() -> None:
    # if the model emits more than one, the final declaration is authoritative
    assert parse_confidence("CONFIDENCE: high\n...\nCONFIDENCE: low") == "low"


def test_is_low() -> None:
    assert is_low("guessing\nCONFIDENCE: low")
    assert not is_low("solid\nCONFIDENCE: high")
    assert not is_low("no marker")


def test_strip_confidence_removes_marker() -> None:
    out = strip_confidence("Here's the fix.\nCONFIDENCE: medium")
    assert "CONFIDENCE" not in out
    assert out.strip() == "Here's the fix."


def test_badge_present_for_levels() -> None:
    assert "high" in (confidence_badge("x\nCONFIDENCE: high") or "")
    assert "verify" in (confidence_badge("x\nCONFIDENCE: low") or "").lower()
    assert confidence_badge("no marker") is None


def test_sentinel_system_is_injected_when_enabled(tmp_path, monkeypatch) -> None:
    # the system prompt should carry the abstention instruction only when on
    from unittest.mock import patch

    from ronin_agent_patterns import FakeProvider, LLMResponse
    from ronin_cli.code_mode import run_code_agent
    from ronin_cli.config import RoninConfig

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    prov = FakeProvider(responses=[LLMResponse(text="hi\nCONFIDENCE: high", stop_reason="end_turn", usage={})])
    cfg = RoninConfig(provider="anthropic", sentinel=True)
    with patch("ronin_cli.code_mode.build_provider", return_value=prov):
        run_code_agent(cfg, "do x", root=tmp_path, console=None, yolo=True)
    assert "Sentinel mode" in prov.calls[0]["system"]
