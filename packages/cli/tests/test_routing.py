from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.routing import classify, pick_model


def test_classify() -> None:
    assert classify("hey") == "simple"
    assert classify("what is groq") == "simple"
    assert classify("fix the bug in @app.py") == "complex"
    assert classify("refactor the auth module") == "complex"
    assert classify("x" * 300) == "complex"


def test_pick_model_routes_by_complexity() -> None:
    cfg = RoninConfig(provider="cerebras", route_fast="llama3.1-8b", route_strong="gpt-oss-120b")
    assert pick_model(cfg, "hey") == "llama3.1-8b"
    assert pick_model(cfg, "refactor the parser") == "gpt-oss-120b"


def test_pick_model_off_when_unset() -> None:
    assert pick_model(RoninConfig(provider="cerebras"), "refactor") is None
