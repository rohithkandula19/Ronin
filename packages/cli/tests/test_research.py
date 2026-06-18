"""Offline tests for the web-research command (`ronin research`).

The agent run is injected so no model, no key, and no network are needed. The
fallback path is exercised with a monkeypatched web_search. Nothing dials out.
"""
from __future__ import annotations

from ronin_cli import research


def test_run_research_calls_injected_runner_and_returns_answer() -> None:
    seen = {}

    def runner(question: str) -> str:
        seen["q"] = question
        return "Argentina won the 2022 World Cup.\nsource: https://example.com"

    out = research.run_research("who won the 2022 world cup", runner=runner)
    assert seen["q"] == "who won the 2022 world cup"
    assert "Argentina" in out
    assert "example.com" in out


def test_run_research_blank_question_asks_for_one() -> None:
    out = research.run_research("   ", runner=lambda q: "should not run")
    assert "ask a question" in out.lower()


def test_run_research_falls_back_to_search_on_runner_error(monkeypatch) -> None:
    # If the agent run blows up, we degrade to a raw web_search (mocked).
    monkeypatch.setattr(
        research, "_fallback_search", lambda q: f"SEARCH RESULTS for {q}"
    )

    def boom(question: str) -> str:
        raise RuntimeError("no model configured")

    out = research.run_research("python release date", runner=boom)
    assert out == "SEARCH RESULTS for python release date"


def test_run_research_empty_answer_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(research, "_fallback_search", lambda q: "FALLBACK")
    out = research.run_research("anything", runner=lambda q: "   ")
    assert out == "FALLBACK"


def test_fallback_search_uses_web_search(monkeypatch) -> None:
    # _fallback_search must route through web_tools.web_search (mocked).
    import ronin_cli.web_tools as web_tools

    monkeypatch.setattr(web_tools, "web_search", lambda q, *a, **k: f"RESULTS:{q}")
    out = research._fallback_search("quantum computing")
    assert out == "RESULTS:quantum computing"


def test_default_runner_passes_question_to_code_agent(monkeypatch) -> None:
    # When no runner is injected, the default runner must call run_code_agent with
    # the question and the research system prompt, and return its output. We stub
    # run_code_agent so no model/network is touched.
    import ronin_cli.code_mode as code_mode

    captured = {}

    class FakeResult:
        output = "the answer with sources"
        error = None

    def fake_run_code_agent(config, task, **kwargs):
        captured["task"] = task
        captured["read_only"] = kwargs.get("read_only")
        captured["base_system"] = kwargs.get("base_system")
        return FakeResult()

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run_code_agent)

    out = research.run_research("what is rust", config=object(), root=".")
    assert out == "the answer with sources"
    assert captured["task"] == "what is rust"
    assert captured["read_only"] is True
    assert captured["base_system"] == research.RESEARCH_SYSTEM
