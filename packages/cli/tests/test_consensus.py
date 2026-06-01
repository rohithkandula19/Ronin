"""Tests for multi-model consensus. The per-model agent runs and the judge
provider are both faked, so no network/LLM is touched — the test covers the
fan-out + synthesis orchestration."""
from __future__ import annotations

from ro_claude_kit_agent_patterns import FakeProvider, LLMResponse

from ro_claude_kit_cli import code_mode, consensus
from ro_claude_kit_cli.code_mode import CodeRunResult
from ro_claude_kit_cli.config import CSKConfig


def _cfg() -> CSKConfig:
    return CSKConfig(provider="anthropic", anthropic_api_key="sk-x")


def test_parse_model_spec() -> None:
    assert consensus.parse_model_spec("anthropic") == {"provider": "anthropic"}
    # only the FIRST colon splits provider from model (models can contain colons)
    assert consensus.parse_model_spec("openrouter:qwen/qwen3-coder:free") == {
        "provider": "openrouter", "model": "qwen/qwen3-coder:free"}


def test_run_consensus_fans_out_and_synthesizes(monkeypatch) -> None:
    def fake_run(config, task, **kwargs):
        assert kwargs["read_only"] is True
        return CodeRunResult(success=True, output=f"answer from {config.provider}", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    # judge provider returns a canned synthesis
    monkeypatch.setattr(consensus, "build_single_provider",
                        lambda cfg: FakeProvider(responses=[LLMResponse(text="SYNTHESIZED ANSWER")]))

    result = consensus.run_consensus(
        _cfg(), "what's the best approach?",
        specs=[{"provider": "anthropic"}, {"provider": "gemini"}],
    )
    assert len(result.answers) == 2
    assert {a.label.split(":")[0] for a in result.answers} == {"anthropic", "gemini"}
    assert all(a.ok for a in result.answers)
    assert result.consensus == "SYNTHESIZED ANSWER"


def test_run_consensus_captures_model_failure(monkeypatch) -> None:
    def fake_run(config, task, **kwargs):
        if config.provider == "gemini":
            return CodeRunResult(success=False, output="", iterations=1, error="rate limited")
        return CodeRunResult(success=True, output="good answer", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    monkeypatch.setattr(consensus, "build_single_provider",
                        lambda cfg: FakeProvider(responses=[LLMResponse(text="SYNTH")]))

    result = consensus.run_consensus(
        _cfg(), "q", specs=[{"provider": "anthropic"}, {"provider": "gemini"}])
    by_provider = {a.label.split(":")[0]: a for a in result.answers}
    assert by_provider["anthropic"].ok is True
    assert by_provider["gemini"].ok is False
    assert by_provider["gemini"].error == "rate limited"
    assert len(result.succeeded) == 1


def test_synthesize_single_answer_skips_judge(monkeypatch) -> None:
    # if build_single_provider were called it'd raise — proving the judge is skipped
    def boom(_cfg):
        raise AssertionError("judge should not run for a single answer")

    monkeypatch.setattr(consensus, "build_single_provider", boom)
    answers = [consensus.ModelAnswer("anthropic:x", "the one answer", ok=True)]
    assert consensus.synthesize(_cfg(), "q", answers, None) == "the one answer"


def test_synthesize_no_answers_message() -> None:
    answers = [consensus.ModelAnswer("a", "", ok=False, error="boom")]
    out = consensus.synthesize(_cfg(), "q", answers, None)
    assert "no model produced an answer" in out
