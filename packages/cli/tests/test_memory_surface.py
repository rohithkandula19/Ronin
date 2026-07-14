"""Ronin v1.1 / Phase 6 — the memory surface (`remember` / `forget` / `timeline`)
and, more importantly, the SECRET FLOOR on the memory store.

Memories are injected into the agent's system prompt on every future run, so a
stored key would be re-sent to the provider forever. The floor therefore lives in
``memory_store.add_memory`` itself — not just the CLI — so the agent's own
``remember`` tool cannot persist a secret either.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli import memory_store
from ronin_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # memory_store honors RONIN_HOME — never touch the real ~/.ronin in tests.
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    return tmp_path


# ---------- the secret floor (the point of this change) ----------

# Assembled at RUNTIME from fragments on purpose. A secret-shaped *literal* in
# this file would be a credential committed to the repository — GitGuardian and
# CodeQL flag that, correctly, and they should. Splitting the prefix means no
# secret pattern exists in the committed source, while the scanner still sees a
# complete, realistic key when the test runs. These fixtures keep their teeth:
# if a fragment stopped looking like a secret, add_memory would happily store it
# and every test below would fail.
_BODY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8"

SECRETS = [
    "my token is " + "ghp" + "_" + _BODY,
    "key " + "sk-" + "ant-api03-" + _BODY + "g9h0",
    "AKIA" + "2E0A8F3B5C7D9E1F",
]


def test_fixtures_really_look_like_secrets():
    """Guard the guard: if fragmenting the literals accidentally broke the secret
    pattern, the refusal tests below would pass vacuously. They must not."""
    for s in SECRETS:
        assert memory_store.secret_labels(s), f"fixture no longer looks like a secret: {s!r}"


@pytest.mark.parametrize("secret", SECRETS)
def test_remember_refuses_a_secret(secret: str):
    r = runner.invoke(app, ["remember", secret])
    assert r.exit_code == 1, r.output
    assert "looks like a secret" in r.output
    assert memory_store.list_memories() == []      # nothing persisted


@pytest.mark.parametrize("secret", SECRETS)
def test_store_layer_refuses_secret_not_just_the_cli(secret: str):
    # defense in depth: the agent's own `remember` tool calls add_memory directly.
    assert memory_store.add_memory(secret) is False
    assert memory_store.list_memories() == []


def test_placeholder_is_not_treated_as_a_secret():
    # a documented placeholder is not a key — don't block legitimate facts
    placeholder = "set " + "sk-" + "ant-REDACTED-EXAMPLE in .env"
    assert memory_store.add_memory(placeholder) is True


# ---------- remember / forget / timeline ----------

def test_remember_stores_a_fact():
    r = runner.invoke(app, ["remember", "I", "use", "Groq"])
    assert r.exit_code == 0, r.output
    assert "remembered" in r.output
    assert "I use Groq" in memory_store.list_memories()


def test_remember_dedups():
    runner.invoke(app, ["remember", "I", "use", "Groq"])
    r = runner.invoke(app, ["remember", "I", "use", "Groq"])
    assert r.exit_code == 0
    assert "already known" in r.output
    assert memory_store.list_memories().count("I use Groq") == 1


def test_forget_matching():
    runner.invoke(app, ["remember", "I", "use", "Groq"])
    r = runner.invoke(app, ["forget", "Groq"])
    assert r.exit_code == 0, r.output
    assert "forgot 1" in r.output
    assert memory_store.list_memories() == []


def test_forget_all():
    runner.invoke(app, ["remember", "one", "fact"])
    runner.invoke(app, ["remember", "another", "fact"])
    r = runner.invoke(app, ["forget", "--all"])
    assert r.exit_code == 0, r.output
    assert memory_store.list_memories() == []


def test_forget_without_pattern_exits_2():
    r = runner.invoke(app, ["forget"])
    assert r.exit_code == 2


def test_timeline_is_chronological():
    runner.invoke(app, ["remember", "first", "fact"])
    runner.invoke(app, ["remember", "second", "fact"])
    r = runner.invoke(app, ["timeline"])
    assert r.exit_code == 0, r.output
    assert r.output.index("first fact") < r.output.index("second fact")


def test_timeline_empty():
    r = runner.invoke(app, ["timeline"])
    assert r.exit_code == 0
    assert "nothing remembered yet" in r.output


def test_timeline_json():
    runner.invoke(app, ["remember", "I", "use", "Groq"])
    r = runner.invoke(app, ["timeline", "--format", "json"])
    assert r.exit_code == 0, r.output
    assert '"facts"' in r.output and "I use Groq" in r.output


def test_memory_file_is_owner_only(isolated_memory: Path):
    """The memory file is user data on disk — create it 0600, not whatever the
    umask happens to allow."""
    import os

    assert memory_store.add_memory("I use Groq") is True
    p = isolated_memory / "memory.json"
    assert p.is_file()
    assert (p.stat().st_mode & 0o777) == 0o600, oct(p.stat().st_mode & 0o777)
