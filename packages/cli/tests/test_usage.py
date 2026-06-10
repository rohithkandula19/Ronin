from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.usage import (
    PRICING,
    Summary,
    estimate_cost,
    load_records,
    record_usage,
    summarize,
)


def test_estimate_cost_known_model() -> None:
    # Sonnet: $3 input / $15 output per 1M tokens
    cost = estimate_cost("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(18.0)


def test_estimate_cost_unknown_model_is_zero() -> None:
    assert estimate_cost("unknown-model-xyz", 100_000, 100_000) == 0.0


def test_estimate_cost_local_model_is_zero() -> None:
    assert estimate_cost("llama3.1", 1_000_000, 1_000_000) == 0.0


def test_record_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    r1 = record_usage(
        command="ask", provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=10_000, output_tokens=2_000, path=path,
    )
    r2 = record_usage(
        command="run", provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=5_000, output_tokens=1_000, path=path,
    )
    assert path.exists()
    records = load_records(path)
    assert len(records) == 2
    assert records[0].input_tokens == 10_000
    assert r1.cost_usd > 0
    assert r2.cost_usd > 0


def test_zero_usage_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    record_usage("ask", "anthropic", "claude-sonnet-4-6", 0, 0, path=path)
    assert not path.exists()


def test_summarize_groups_by_model_and_day(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    record_usage("ask", "anthropic", "claude-sonnet-4-6", 1000, 500, path=path)
    record_usage("ask", "anthropic", "claude-opus-4-7", 200, 100, path=path)
    record_usage("ask", "anthropic", "claude-sonnet-4-6", 2000, 800, path=path)

    summary = summarize(load_records(path))
    assert summary.total_calls == 3
    assert summary.by_model["claude-sonnet-4-6"].total_calls == 2
    assert summary.by_model["claude-opus-4-7"].total_calls == 1
    assert summary.by_model["claude-sonnet-4-6"].total_input_tokens == 3000

    # All recorded today (UTC)
    assert len(summary.by_day) == 1


def test_pricing_table_has_expected_models() -> None:
    assert "claude-sonnet-4-6" in PRICING
    assert "claude-opus-4-7" in PRICING
    assert "gpt-4o-mini" in PRICING
    assert "llama3.1" in PRICING
