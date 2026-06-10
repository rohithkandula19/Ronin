from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.demo_brain import demo_answer
from ronin_cli.tools import build_tools


def _demo_tools():
    return build_tools(RoninConfig(demo_mode=True))


def test_subscriptions_question() -> None:
    result = demo_answer("how many active subscriptions do we have", _demo_tools())
    assert result.success
    assert "active subscriptions" in result.output.lower()
    assert "MRR" in result.output
    kinds = [s.kind for s in result.trace]
    assert "tool_call" in kinds
    assert "final" in kinds


def test_mrr_question() -> None:
    result = demo_answer("what's our MRR right now?", _demo_tools())
    assert "MRR" in result.output


def test_customers_question() -> None:
    result = demo_answer("list our customers", _demo_tools())
    assert "alice@acme.com" in result.output
    assert "bob@beta.io" in result.output


def test_issues_question_with_state_filter() -> None:
    result = demo_answer("what ENG issues are in progress?", _demo_tools())
    assert "Stripe webhook flake" in result.output
    # Should detect the "In Progress" filter
    tool_calls = [s for s in result.trace if s.kind == "tool_call"]
    assert any(c.content["input"].get("state") == "In Progress" for c in tool_calls)


def test_teams_question() -> None:
    result = demo_answer("list linear teams", _demo_tools())
    assert "ENG" in result.output
    assert "DES" in result.output


def test_charges_question() -> None:
    result = demo_answer("show me recent charges", _demo_tools())
    assert "ch_" in result.output  # at least one charge id rendered


def test_fallback_for_unknown_question() -> None:
    result = demo_answer("what's the meaning of life?", _demo_tools())
    assert "demo brain" in result.output.lower()
    assert "ANTHROPIC_API_KEY" in result.output


def test_no_real_key_routes_to_demo_brain(monkeypatch) -> None:
    """End-to-end check via run_ask: demo mode + no key → demo_brain answer, no anthropic call."""
    from ronin_cli.runner import run_ask

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = RoninConfig(demo_mode=True)
    result = run_ask(config, "how many active subscriptions?", console=None)

    assert result.success
    assert result.demo_mode is True
    assert "MRR" in result.output
    # If we'd routed to ReActAgent, it would have crashed without an API key.
