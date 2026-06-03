"""Edge-case coverage for spec/diff/pytest parsers."""
from __future__ import annotations

import pytest

from ronin_cli.consensus import parse_model_spec
from ronin_cli.kaizen import parse_pytest


# ---- parse_model_spec ----

def test_spec_provider_only() -> None:
    assert parse_model_spec("anthropic") == {"provider": "anthropic", "model": None} \
        or parse_model_spec("anthropic")["provider"] == "anthropic"


def test_spec_provider_model() -> None:
    s = parse_model_spec("cerebras:gpt-oss-120b")
    assert s["provider"] == "cerebras" and s["model"] == "gpt-oss-120b"


def test_spec_model_with_colons_and_slashes() -> None:
    # OpenRouter free models have both a slash and a trailing :free
    s = parse_model_spec("openrouter:deepseek/deepseek-v4-flash:free")
    assert s["provider"] == "openrouter"
    assert s["model"] == "deepseek/deepseek-v4-flash:free"


def test_spec_strips_whitespace() -> None:
    s = parse_model_spec("  groq : llama-3.3-70b  ")
    assert s["provider"] == "groq" and s["model"] == "llama-3.3-70b"


# ---- parse_pytest ----

@pytest.mark.parametrize("out,passed,total,failed", [
    ("10 passed in 1s", True, 10, 0),
    ("908 passed, 41 warnings in 9.10s", True, 908, 0),
    ("2 failed, 100 passed in 3s", False, 102, 2),
    ("1 error, 5 passed in 1s", False, 6, 1),
    ("3 errors, 5 passed in 1s", False, 8, 3),
    ("", False, 0, 0),
    ("collected 0 items", False, 0, 0),
])
def test_parse_pytest_variants(out: str, passed: bool, total: int, failed: int) -> None:
    f = parse_pytest(out)
    assert f.passed is passed
    assert f.total == total
    assert f.failed == failed


def test_parse_pytest_summary_is_last_line() -> None:
    f = parse_pytest("....\nsome noise\n5 passed in 2s")
    assert "5 passed" in f.summary
