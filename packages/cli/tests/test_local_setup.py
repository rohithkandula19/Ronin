"""Tests for `ronin local` — PURE functions only (no network, no subprocess, no disk).

Covers the unit-testable seams of ``ronin_cli.local_setup``:
  * ``recommend_model``   — the RAM → coding-model table + boundaries.
  * ``parse_ollama_tags`` — both `ollama list` table output AND `/api/tags` JSON.
  * ``ollama_installed``  — binary-presence detection from a `which`-style string.
"""
from __future__ import annotations

import json

from ronin_cli import local_setup as L


# --------------------------------------------------------------------------- #
# recommend_model
# --------------------------------------------------------------------------- #
def test_recommend_model_low_ram() -> None:
    # < 8 GB → the small 1.5b model.
    assert L.recommend_model(4) == "qwen2.5-coder:1.5b"
    assert L.recommend_model(7) == "qwen2.5-coder:1.5b"


def test_recommend_model_mid_ram() -> None:
    # 8 ..< 16 → 7b (the common laptop default).
    assert L.recommend_model(8) == "qwen2.5-coder:7b"
    assert L.recommend_model(12) == "qwen2.5-coder:7b"
    assert L.recommend_model(15) == "qwen2.5-coder:7b"


def test_recommend_model_high_ram() -> None:
    # 16 ..< 32 → 14b.
    assert L.recommend_model(16) == "qwen2.5-coder:14b"
    assert L.recommend_model(24) == "qwen2.5-coder:14b"
    assert L.recommend_model(31) == "qwen2.5-coder:14b"


def test_recommend_model_workstation() -> None:
    # >= 32 → the largest 32b model.
    assert L.recommend_model(32) == "qwen2.5-coder:32b"
    assert L.recommend_model(64) == "qwen2.5-coder:32b"
    assert L.recommend_model(128) == "qwen2.5-coder:32b"


def test_recommend_model_boundaries_are_exclusive_floors() -> None:
    # Each table ceiling is an exclusive floor: exactly-at-ceiling jumps up a tier.
    assert L.recommend_model(7) != L.recommend_model(8)
    assert L.recommend_model(15) != L.recommend_model(16)
    assert L.recommend_model(31) != L.recommend_model(32)


def test_recommend_model_nonpositive_ram_is_safe_smallest() -> None:
    # Unknown / unreadable RAM (0 or negative) → smallest model, never a crash.
    assert L.recommend_model(0) == "qwen2.5-coder:1.5b"
    assert L.recommend_model(-1) == "qwen2.5-coder:1.5b"


def test_recommend_model_always_returns_a_real_pull_tag() -> None:
    # Every output is a concrete qwen2.5-coder tag (a real `ollama pull` target).
    for ram in (0, 4, 8, 16, 32, 64, 256):
        tag = L.recommend_model(ram)
        assert tag.startswith("qwen2.5-coder:")
        assert ":" in tag


# --------------------------------------------------------------------------- #
# parse_ollama_tags — JSON form (/api/tags and OpenAI-compat /v1/models)
# --------------------------------------------------------------------------- #
def test_parse_tags_json_api_tags_shape() -> None:
    body = json.dumps({
        "models": [
            {"name": "qwen2.5-coder:7b", "size": 4700000000},
            {"name": "llama3.1:latest", "size": 4900000000},
        ]
    })
    assert L.parse_ollama_tags(body) == ["qwen2.5-coder:7b", "llama3.1:latest"]


def test_parse_tags_json_openai_compat_shape() -> None:
    # The /v1/models OpenAI-compat shape uses {"data": [{"id": ...}]}.
    body = json.dumps({"data": [{"id": "qwen2.5-coder:1.5b"}, {"id": "mistral:latest"}]})
    assert L.parse_ollama_tags(body) == ["qwen2.5-coder:1.5b", "mistral:latest"]


def test_parse_tags_json_model_key_fallback() -> None:
    body = json.dumps({"models": [{"model": "phi3:mini"}]})
    assert L.parse_ollama_tags(body) == ["phi3:mini"]


def test_parse_tags_json_empty_models() -> None:
    assert L.parse_ollama_tags(json.dumps({"models": []})) == []


# --------------------------------------------------------------------------- #
# parse_ollama_tags — table form (`ollama list`)
# --------------------------------------------------------------------------- #
def test_parse_tags_table_with_header() -> None:
    text = (
        "NAME                    ID              SIZE      MODIFIED\n"
        "qwen2.5-coder:7b        abc123          4.7 GB    2 days ago\n"
        "llama3.1:latest         def456          4.9 GB    3 weeks ago\n"
    )
    assert L.parse_ollama_tags(text) == ["qwen2.5-coder:7b", "llama3.1:latest"]


def test_parse_tags_table_without_header() -> None:
    text = "qwen2.5-coder:14b   xyz   9 GB   1 hour ago"
    assert L.parse_ollama_tags(text) == ["qwen2.5-coder:14b"]


def test_parse_tags_table_dedupes_and_drops_blanks() -> None:
    text = (
        "NAME    ID    SIZE    MODIFIED\n"
        "qwen2.5-coder:7b  a  4.7 GB  x\n"
        "\n"
        "qwen2.5-coder:7b  a  4.7 GB  x\n"
        "mistral:latest    b  4 GB    y\n"
    )
    assert L.parse_ollama_tags(text) == ["qwen2.5-coder:7b", "mistral:latest"]


# --------------------------------------------------------------------------- #
# parse_ollama_tags — degenerate input
# --------------------------------------------------------------------------- #
def test_parse_tags_empty_and_whitespace() -> None:
    assert L.parse_ollama_tags("") == []
    assert L.parse_ollama_tags("   \n  \t ") == []


def test_parse_tags_malformed_json_does_not_raise() -> None:
    # A truncated/garbage JSON-looking string must not raise — returns [].
    assert L.parse_ollama_tags('{"models": [') == []


def test_parse_tags_none_safe() -> None:
    # Defensive: None coerces to [] rather than blowing up.
    assert L.parse_ollama_tags(None) == []  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# ollama_installed
# --------------------------------------------------------------------------- #
def test_ollama_installed_with_path() -> None:
    assert L.ollama_installed("/usr/local/bin/ollama") is True
    assert L.ollama_installed("/opt/homebrew/bin/ollama") is True


def test_ollama_installed_strips_whitespace() -> None:
    assert L.ollama_installed("  /usr/local/bin/ollama\n") is True


def test_ollama_installed_missing() -> None:
    assert L.ollama_installed("") is False
    assert L.ollama_installed("   ") is False
    assert L.ollama_installed(None) is False  # type: ignore[arg-type]


def test_ollama_installed_not_found_messages() -> None:
    # `which` misses can print a not-found message to stdout — treat as not installed.
    assert L.ollama_installed("ollama not found") is False
    assert L.ollama_installed("no ollama in (/usr/bin /bin)") is False
