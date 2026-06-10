"""Tests for autonomous-run notifications."""
from __future__ import annotations

from ronin_cli import notify
from ronin_cli.notify import build_payload, format_report, post_webhook


def test_format_report_basic() -> None:
    txt = format_report({"patched": 2, "total": 3, "failed-tests": 1}, ["fix auth", "add cache"])
    assert "2/3" in txt and "patches" in txt
    assert "failed tests" in txt
    assert "• fix auth" in txt and "• add cache" in txt
    assert "ronin patches" in txt


def test_format_report_singular() -> None:
    txt = format_report({"patched": 1, "total": 1})
    assert "patch" in txt and "patches" not in txt.split("\n")[0]


def test_format_report_zero_patched() -> None:
    txt = format_report({"patched": 0, "total": 2, "no-change": 2})
    assert "0/2" in txt and "no-change" in txt
    assert "ronin patches" not in txt           # nothing to review


def test_build_payload() -> None:
    assert build_payload("hello") == {"text": "hello"}


def test_post_webhook_empty_url() -> None:
    assert post_webhook("", "x") is False


def test_post_webhook_success(monkeypatch) -> None:
    import httpx

    class _R:
        status_code = 200
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    assert post_webhook("https://hooks.example/x", "hi") is True


def test_post_webhook_error_is_soft(monkeypatch) -> None:
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "post", boom)
    assert post_webhook("https://hooks.example/x", "hi") is False


def test_post_webhook_non_2xx(monkeypatch) -> None:
    import httpx

    class _R:
        status_code = 500
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    assert post_webhook("https://hooks.example/x", "hi") is False
