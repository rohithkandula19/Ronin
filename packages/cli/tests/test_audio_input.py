from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

from ro_claude_kit_cli import audio
from ro_claude_kit_cli.config import CSKConfig


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload or {}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("e", request=None, response=None)
    def json(self):
        return self._p


def test_transcribe_groq_uses_turbo(monkeypatch) -> None:
    captured = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["model"] = kw["data"]["model"]
        return _Resp(200, {"text": "hello world"})

    monkeypatch.setattr(httpx, "post", fake_post)
    p = Path(tempfile.mktemp(suffix=".wav"))
    p.write_bytes(b"RIFFfake")
    try:
        text = audio.transcribe(p, CSKConfig(provider="groq", openai_api_key="k"))
    finally:
        p.unlink()
    assert text == "hello world"
    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["model"] == "whisper-large-v3-turbo"   # groq → turbo


def test_transcribe_404_is_friendly(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(404))
    p = Path(tempfile.mktemp(suffix=".wav"))
    p.write_bytes(b"x")
    try:
        try:
            audio.transcribe(p, CSKConfig(provider="cerebras", openai_api_key="k"))
            assert False, "should have raised"
        except RuntimeError as e:
            assert "Whisper" in str(e)
    finally:
        p.unlink()
