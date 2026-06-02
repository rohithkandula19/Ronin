"""Tests for the eval-driven model bake-off. run_eval is faked so no real model
calls happen — the test covers scoring, cost estimation, and the recommendation."""
from __future__ import annotations

from ronin_cli import bench
from ronin_cli.agent_eval import EvalTask, TaskOutcome
from ronin_cli.config import RoninConfig


def _cfg() -> RoninConfig:
    return RoninConfig(provider="anthropic", anthropic_api_key="sk-x")


def _outcomes(n_pass: int, n_total: int, tokens_each: int = 1000,
              elapsed_each: float = 1.0) -> list[TaskOutcome]:
    task = EvalTask("t", "p", lambda r, root: True)
    out = []
    for i in range(n_total):
        out.append(TaskOutcome(task, passed=i < n_pass, iterations=1,
                               elapsed=elapsed_each, tokens=tokens_each))
    return out


def test_cost_estimate_free_vs_paid() -> None:
    free = bench.BenchRow("gemini:x", "gemini", 6, 6, 100_000, 5.0)
    assert free.est_cost == 0.0
    paid = bench.BenchRow("anthropic:x", "anthropic", 6, 6, 1_000_000, 5.0)
    assert paid.est_cost == bench.COST_PER_MTOK["anthropic"]  # 1M tokens × rate
    unknown = bench.BenchRow("custom:x", "custom", 6, 6, 1000, 5.0)
    assert unknown.est_cost is None


def test_run_bench_scores_and_recommends_cheapest_passing(monkeypatch) -> None:
    # anthropic: perfect but paid; gemini: also passes and is free → gemini wins
    def fake_eval(cfg, **kwargs):
        if cfg.provider == "gemini":
            return _outcomes(6, 6)
        if cfg.provider == "anthropic":
            return _outcomes(6, 6)
        return _outcomes(2, 6)  # ollama: below bar

    monkeypatch.setattr(bench, "run_eval", fake_eval)
    result = bench.run_bench(_cfg(), [
        {"provider": "anthropic"}, {"provider": "gemini"}, {"provider": "ollama", "model": "llama3.1"},
    ], threshold=0.8)

    assert len(result.rows) == 3
    by = {r.provider: r for r in result.rows}
    assert by["anthropic"].rate == 1.0
    assert by["ollama"].rate < 0.8
    # free + passing beats paid + passing
    assert "gemini" in result.recommendation
    assert "free" in result.recommendation


def test_run_bench_no_model_passes(monkeypatch) -> None:
    monkeypatch.setattr(bench, "run_eval", lambda cfg, **kw: _outcomes(1, 6))
    result = bench.run_bench(_cfg(), [{"provider": "anthropic"}, {"provider": "gemini"}],
                             threshold=0.9)
    assert "no model cleared" in result.recommendation


def test_run_bench_captures_dead_model(monkeypatch) -> None:
    def fake_eval(cfg, **kwargs):
        if cfg.provider == "gemini":
            raise RuntimeError("connection refused")
        return _outcomes(6, 6)

    monkeypatch.setattr(bench, "run_eval", fake_eval)
    result = bench.run_bench(_cfg(), [{"provider": "anthropic"}, {"provider": "gemini"}])
    gem = next(r for r in result.rows if r.provider == "gemini")
    assert gem.error is not None and gem.total == 0
    # the healthy model still gets recommended
    assert "anthropic" in result.recommendation


def test_parse_specs_or_exit(monkeypatch) -> None:
    from rich.console import Console
    specs = bench.parse_specs_or_exit("anthropic,gemini:gemini-flash-latest", Console())
    assert specs == [{"provider": "anthropic"},
                     {"provider": "gemini", "model": "gemini-flash-latest"}]
