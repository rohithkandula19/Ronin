from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from typer.testing import CliRunner

from ronin_cli.briefing_slack import post_briefing_to_slack, to_slack_mrkdwn
from ronin_cli.main import app


runner = CliRunner()


# ---------- mrkdwn conversion ----------

def test_bold_double_star_becomes_single_star() -> None:
    assert to_slack_mrkdwn("**MRR:** $334") == "*MRR:* $334"


def test_headings_become_bold() -> None:
    md = "# Title\n## Subtitle\nbody"
    out = to_slack_mrkdwn(md)
    assert "*Title*" in out
    assert "*Subtitle*" in out


def test_bullets_and_inline_left_alone() -> None:
    md = "- a bullet\n  - nested\n`code` and _emphasis_"
    out = to_slack_mrkdwn(md)
    assert "- a bullet" in out
    assert "  - nested" in out
    assert "`code`" in out
    assert "_emphasis_" in out


# ---------- post helper ----------

def _ok_response(payload: dict | None = None) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = {"ok": True, "ts": "1731343000.000100", **(payload or {})}
    response.raise_for_status = MagicMock()
    return response


def _err_response(error: str) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = {"ok": False, "error": error}
    response.raise_for_status = MagicMock()
    return response


def test_post_validates_inputs() -> None:
    with pytest.raises(ValueError, match="bot_token"):
        post_briefing_to_slack("", "#founders", "hi")
    with pytest.raises(ValueError, match="channel"):
        post_briefing_to_slack("xoxb-x", "", "hi")


def test_post_happy_path_uses_bot_token_and_converts_markdown() -> None:
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _ok_response()

    resp = post_briefing_to_slack("xoxb-test", "#founders", "**MRR:** $334\n# heading", http=http)

    assert resp["ok"] is True
    args, kwargs = http.post.call_args
    assert args[0].endswith("/chat.postMessage")
    assert kwargs["headers"]["Authorization"] == "Bearer xoxb-test"
    body = kwargs["json"]
    assert body["channel"] == "#founders"
    # **bold** → *bold* and # heading → *heading*
    assert "*MRR:*" in body["text"]
    assert "*heading*" in body["text"]
    assert body["mrkdwn"] is True


def test_post_raises_on_slack_error() -> None:
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _err_response("channel_not_found")
    with pytest.raises(RuntimeError, match="channel_not_found"):
        post_briefing_to_slack("xoxb-test", "#nope", "hi", http=http)


# ---------- end-to-end via the CLI ----------

def test_briefing_slack_flag_without_token_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    runner.invoke(app, ["init", "--demo", "-y"])

    r = runner.invoke(app, ["util", "briefing", "--slack", "#founders", "--raw"])
    assert r.exit_code == 2
    assert "SLACK_BOT_TOKEN" in r.stdout


def test_briefing_slack_flag_posts_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: env var token → CLI posts via mocked httpx.Client."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    runner.invoke(app, ["init", "--demo", "-y"])

    posted: list[dict] = []

    def fake_post(self, url, **kwargs):  # noqa: ANN001
        posted.append({"url": url, **kwargs})
        return _ok_response()

    with patch.object(httpx.Client, "post", fake_post):
        r = runner.invoke(app, ["util", "briefing", "--slack", "#founders", "--raw"])

    assert r.exit_code == 0, r.stdout
    assert "posted to" in r.stdout
    assert posted, "Slack endpoint should have been hit"
    assert posted[0]["url"].endswith("/chat.postMessage")
    assert posted[0]["json"]["channel"] == "#founders"
