"""Tests for model debate — prompt builders + a full mocked debate flow."""
from __future__ import annotations

from unittest.mock import patch

from ronin_cli.config import RoninConfig
from ronin_cli.debate import critique_prompt, opening_prompt, run_debate


def test_opening_prompt() -> None:
    p = opening_prompt("Queue or cron?")
    assert "Queue or cron?" in p and "reasoning" in p.lower()


def test_critique_prompt_includes_others_and_round() -> None:
    p = critique_prompt("Q?", [("anthropic", "use a queue"), ("gemini", "use cron")],
                        your_last="I said cron", round_num=2)
    assert "round 2" in p
    assert "anthropic said" in p and "use a queue" in p
    assert "use cron" in p
    assert "I said cron" in p
    assert "REVISED" in p


def test_critique_prompt_no_others() -> None:
    p = critique_prompt("Q?", [], your_last="x", round_num=2)
    assert "no other answers" in p


def test_run_debate_two_rounds_then_synthesis() -> None:
    cfg = RoninConfig(provider="anthropic")
    specs = [{"provider": "anthropic"}, {"provider": "gemini"}]

    # each _ask call returns a canned answer; the judge returns a synthesis
    calls = {"n": 0}

    def fake_ask(base, spec, system, prompt, max_tokens=1200):
        if "moderator" in system.lower():
            return "SYNTHESIS: use a queue"
        calls["n"] += 1
        return f"answer {calls['n']}"

    with patch("ronin_cli.debate._ask", side_effect=fake_ask):
        res = run_debate(cfg, "Queue or cron?", specs, rounds=2)

    # 2 panelists × 2 rounds = 4 answer calls, each panelist has 2 rounds recorded
    assert all(len(p.rounds) == 2 for p in res.panelists)
    assert len(res.participated) == 2
    assert "use a queue" in res.synthesis


def test_run_debate_empty_specs() -> None:
    res = run_debate(RoninConfig(), "Q?", [])
    assert res.panelists == [] and res.synthesis == ""


def test_run_debate_one_model_failing_does_not_sink_it() -> None:
    cfg = RoninConfig(provider="anthropic")
    specs = [{"provider": "anthropic"}, {"provider": "gemini"}]

    def fake_ask(base, spec, system, prompt, max_tokens=1200):
        if "moderator" in system.lower():
            return "final"
        if spec.get("provider") == "gemini":
            raise RuntimeError("gemini down")
        return "anthropic answer"

    with patch("ronin_cli.debate._ask", side_effect=fake_ask):
        res = run_debate(cfg, "Q?", specs, rounds=1)
    # one failed, but the debate still produced a synthesis from the survivor
    assert any(not p.ok for p in res.panelists)
    assert res.synthesis == "final"
