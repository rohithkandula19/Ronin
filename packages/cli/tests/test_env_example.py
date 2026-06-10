"""Tests for .env.example generation."""
from __future__ import annotations

from ronin_cli.env_example import mask_env


def test_blanks_secret_values_keeps_keys() -> None:
    env = "# config\nAPI_KEY=sk-supersecret123456\nDB_PASSWORD=hunter2\n"
    r = mask_env(env)
    assert "API_KEY=\n" in r["text"]
    assert "DB_PASSWORD=\n" in r["text"]
    assert "sk-supersecret" not in r["text"]
    assert r["keys"] == ["API_KEY", "DB_PASSWORD"]


def test_keeps_safe_defaults() -> None:
    env = "DEBUG=true\nPORT=8080\nHOST=localhost\nSECRET=abcdef0123456789abcdef\n"
    r = mask_env(env)
    assert "DEBUG=true" in r["text"]
    assert "PORT=8080" in r["text"]
    assert "HOST=localhost" in r["text"]
    assert "SECRET=\n" in r["text"]      # the long opaque value is blanked


def test_preserves_comments_and_export() -> None:
    env = "# top comment\nexport TOKEN=ghp_secretsecret\n\n# trailing\n"
    r = mask_env(env)
    assert "# top comment" in r["text"]
    assert "export TOKEN=" in r["text"] and "ghp_secret" not in r["text"]
    assert r["keys"] == ["TOKEN"]
