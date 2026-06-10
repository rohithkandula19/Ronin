"""Tests for the api-test assertion engine."""
from __future__ import annotations

from ronin_cli.api_test import check_response, parse_expect


def test_parse_expect() -> None:
    assert parse_expect(["a.b=1", "x = y"]) == [("a.b", "1"), ("x", "y")]


def test_status_assertion() -> None:
    assert check_response(200, {}, expect_status=200)["passed"] is True
    r = check_response(404, {}, expect_status=200)
    assert r["passed"] is False and r["results"][0]["actual"] == 404


def test_field_assertions_with_coercion() -> None:
    body = {"data": [{"id": 5, "active": True}]}
    r = check_response(200, body, expect_status=200,
                       expect=[("data.0.id", "5"), ("data.0.active", "true")])
    assert r["passed"] is True            # 5==‘5’ numeric, True==‘true’ bool


def test_field_mismatch_and_missing() -> None:
    body = {"name": "ada"}
    r = check_response(200, body, expect=[("name", "bob"), ("missing.key", "x")])
    assert r["passed"] is False
    assert r["results"][0]["passed"] is False     # wrong value
    assert r["results"][1]["passed"] is False     # missing path
