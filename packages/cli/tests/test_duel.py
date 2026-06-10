"""Tests for Ronin Duel — a rival model red-teaming a diff."""
from __future__ import annotations

from ronin_agent_patterns.providers.fake import FakeProvider
from ronin_agent_patterns.providers.base import LLMResponse

from ronin_cli.config import RoninConfig
from ronin_cli.duel import parse_verdict, review_diff

_DIFF = "diff --git a/x.py b/x.py\n+balance -= amount  # no lock\n"


def _fake(text: str) -> FakeProvider:
    return FakeProvider(responses=[LLMResponse(text=text)])


def test_parse_clean_pass() -> None:
    v = parse_verdict("Looks fine.\nVERDICT: PASS", "gemini")
    assert v.passed and v.blocker_count == 0


def test_parse_blockers() -> None:
    text = ("BLOCKER: x.py:1 — race condition on balance\n"
            "BLOCKER: x.py:1 — missing null check\n"
            "VERDICT: BLOCK")
    v = parse_verdict(text, "gemini")
    assert not v.passed and v.blocker_count == 2
    assert "race condition" in v.blockers[0]


def test_findings_override_careless_pass() -> None:
    # model listed a blocker but then wrote PASS — findings win, never silent-pass
    v = parse_verdict("BLOCKER: real bug here\nVERDICT: PASS", "gemini")
    assert not v.passed


def test_missing_verdict_line_uses_blockers() -> None:
    assert parse_verdict("BLOCKER: oops", "x").passed is False
    assert parse_verdict("all good, nothing to flag", "x").passed is True


def test_review_diff_with_injected_provider() -> None:
    prov = _fake("BLOCKER: x.py:1 — unguarded balance mutation\nVERDICT: BLOCK")
    v = review_diff(RoninConfig(), _DIFF, {"provider": "gemini", "model": "g"}, provider=prov)
    assert not v.passed and v.blocker_count == 1
    assert v.duelist == "gemini:g"
    # the diff was actually handed to the reviewer
    assert "balance" in prov.calls[0]["messages"][0]["content"]


def test_review_empty_diff_passes_without_call() -> None:
    prov = _fake("unused")
    v = review_diff(RoninConfig(), "   ", provider=prov)
    assert v.passed and not prov.calls   # never bothered the model
