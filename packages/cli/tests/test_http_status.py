"""Tests for HTTP status explanations."""
from __future__ import annotations

from ronin_cli.http_status import explain_status, known_codes


def test_common_codes() -> None:
    assert explain_status(200)["name"] == "OK"
    assert explain_status(404)["category"] == "client error"
    assert explain_status(500)["category"] == "server error"
    assert "rate limited" in explain_status(429)["explanation"]


def test_category_by_class() -> None:
    assert explain_status(301)["category"] == "redirect"
    assert explain_status(201)["category"] == "success"


def test_unknown_is_none() -> None:
    assert explain_status(799) is None


def test_known_codes() -> None:
    k = known_codes()
    assert 200 in k and 418 in k and 503 in k
