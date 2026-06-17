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


# ---------- notify abstraction: backends + Notifier ----------

from ronin_cli.notify import (  # noqa: E402
    LocalBackend,
    Notifier,
    WebhookBackend,
    build_notifier,
)


def test_local_backend_writes_log(tmp_path) -> None:
    log = tmp_path / "notes.log"
    backend = LocalBackend(log, desktop=False)
    assert backend.send("hello world") is True
    assert backend.send("second line") is True
    text = log.read_text()
    assert "hello world" in text
    assert "second line" in text
    # two appended lines, each timestamped in brackets
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2
    assert lines[0].startswith("[") and "]" in lines[0]


def test_local_backend_creates_parent_dir(tmp_path) -> None:
    log = tmp_path / "nested" / "deep" / "notes.log"
    assert LocalBackend(log, desktop=False).send("x") is True
    assert log.exists()


def test_webhook_backend_delegates(monkeypatch) -> None:
    sent = {}

    def fake_post(url, text, *, timeout=15):
        sent["url"] = url
        sent["text"] = text
        return True

    monkeypatch.setattr(notify, "post_webhook", fake_post)
    assert WebhookBackend("https://hook/x").send("ping") is True
    assert sent == {"url": "https://hook/x", "text": "ping"}


def test_notifier_fans_out_and_reports_any_success(tmp_path) -> None:
    log = tmp_path / "n.log"

    class _Fail:
        name = "fail"

        def send(self, text):
            return False

    n = Notifier([LocalBackend(log, desktop=False), _Fail()])
    assert n.notify("hi") is True            # local succeeded
    assert "hi" in log.read_text()
    assert n.backend_names() == ["local", "fail"]


def test_notifier_all_fail_returns_false() -> None:
    class _Fail:
        name = "fail"

        def send(self, text):
            return False

    assert Notifier([_Fail()]).notify("x") is False


def test_notifier_backend_exception_is_isolated(tmp_path) -> None:
    log = tmp_path / "n.log"

    class _Boom:
        name = "boom"

        def send(self, text):
            raise RuntimeError("nope")

    # The exploding backend must not stop the local one from delivering.
    n = Notifier([_Boom(), LocalBackend(log, desktop=False)])
    assert n.notify("survives") is True
    assert "survives" in log.read_text()


def test_build_notifier_local_only(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RONIN_NOTIFY_WEBHOOK", raising=False)
    n = build_notifier(log_path=tmp_path / "n.log", desktop=False)
    assert n.backend_names() == ["local"]


def test_build_notifier_adds_webhook_from_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RONIN_NOTIFY_WEBHOOK", "https://hook/x")
    n = build_notifier(log_path=tmp_path / "n.log", desktop=False)
    assert n.backend_names() == ["local", "webhook"]


def test_build_notifier_explicit_url_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RONIN_NOTIFY_WEBHOOK", raising=False)
    n = build_notifier(log_path=tmp_path / "n.log", webhook_url="https://hook/y", desktop=False)
    assert n.backend_names() == ["local", "webhook"]
