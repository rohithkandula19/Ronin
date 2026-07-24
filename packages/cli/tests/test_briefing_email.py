from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from typer.testing import CliRunner

from ronin_cli.briefing_email import markdown_to_html, send_briefing_email
from ronin_cli.main import app


runner = CliRunner()


# ---------- markdown → html ----------

def test_html_renders_headings() -> None:
    html = markdown_to_html("# Title\n## Subtitle\n\nbody")
    assert "<h1>Title</h1>" in html
    assert "<h2>Subtitle</h2>" in html
    assert "<p>body</p>" in html


def test_html_renders_bullets() -> None:
    html = markdown_to_html("- one\n- two\n  - nested")
    assert html.count("<ul>") >= 1
    assert "<li>one</li>" in html
    assert "<li>nested</li>" in html


def test_html_inline_formatting() -> None:
    html = markdown_to_html("**bold** and `code` and _italic_")
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert "<em>italic</em>" in html


def test_html_doctype_wrapped() -> None:
    html = markdown_to_html("# x")
    assert html.startswith("<!doctype html>")
    assert html.endswith("</body></html>")


# ---------- send ----------

def _ok(payload: dict | None = None) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = payload or {"id": "em_abc123"}
    response.raise_for_status = MagicMock()
    return response


def test_send_validates_inputs() -> None:
    with pytest.raises(ValueError, match="recipient"):
        send_briefing_email("", "hi")
    with pytest.raises(ValueError, match="recipient"):
        send_briefing_email("not-an-email", "hi")


def test_send_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        send_briefing_email("a@b.com", "hi")


def test_send_happy_path_posts_with_html_body() -> None:
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _ok({"id": "em_xxx"})

    resp = send_briefing_email(
        "founder@startup.io",
        "# Founder briefing — 2026-05-11\n\n## 💰 Revenue\n- MRR: **$334**",
        api_key="re_test",
        http=http,
    )
    assert resp == {"id": "em_xxx"}

    _, kwargs = http.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer re_test"
    body = kwargs["json"]
    assert body["to"] == ["founder@startup.io"]
    assert "<h1>" in body["html"]
    assert "<strong>$334</strong>" in body["html"]
    # Plain-text fallback should still be the raw Markdown
    assert "MRR: **$334**" in body["text"]


def test_send_subject_pulled_from_first_h1() -> None:
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _ok()
    send_briefing_email("a@b.com", "# Founder briefing — 2026-05-11\n\nbody", api_key="re_x", http=http)
    assert http.post.call_args.kwargs["json"]["subject"] == "Founder briefing — 2026-05-11"


def test_send_subject_overridable() -> None:
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _ok()
    send_briefing_email("a@b.com", "# x", api_key="re_x", subject="custom", http=http)
    assert http.post.call_args.kwargs["json"]["subject"] == "custom"


# ---------- CLI integration ----------

def test_briefing_email_flag_without_key_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    r = runner.invoke(app, ["util", "briefing", "--email", "a@b.com", "--raw"])
    assert r.exit_code == 2
    assert "RESEND_API_KEY" in r.stdout


def test_briefing_email_flag_sends_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    runner.invoke(app, ["init", "--demo", "-y"])

    posted: list[dict] = []

    def fake_post(self, url, **kwargs):  # noqa: ANN001
        posted.append({"url": url, **kwargs})
        return _ok()

    with patch.object(httpx.Client, "post", fake_post):
        r = runner.invoke(app, ["util", "briefing", "--email", "founder@startup.io", "--raw"])

    assert r.exit_code == 0, r.stdout
    assert "emailed to" in r.stdout
    assert posted, "Resend endpoint should have been hit"
    assert posted[0]["url"].endswith("/emails")
    assert posted[0]["json"]["to"] == ["founder@startup.io"]
