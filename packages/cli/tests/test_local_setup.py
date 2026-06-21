"""Tests for `ronin local` — PURE functions only (no network, no subprocess, no disk).

Covers the unit-testable seams of ``ronin_cli.local_setup``:
  * ``recommend_model``    — the RAM → coding-model table + boundaries.
  * ``parse_ollama_tags``  — both `ollama list` table output AND `/api/tags` JSON.
  * ``ollama_installed``   — binary-presence detection from a `which`-style string.
  * ``embedded_available`` — engine presence via monkeypatched ``find_spec``.
  * ``apply_embedded_config`` — writes provider="local" via the save_config seam.
"""
from __future__ import annotations

import importlib.util
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


# --------------------------------------------------------------------------- #
# embedded_available — engine presence via monkeypatched importlib find_spec
# --------------------------------------------------------------------------- #
def _fake_find_spec(present: set[str]):
    """Build a find_spec stand-in that reports only modules in ``present`` as
    importable (returns a truthy sentinel) and everything else as missing (None).
    Mirrors importlib.util.find_spec without importing anything heavy."""
    def _spec(name: str, *args, **kwargs):  # noqa: ANN002, ANN003
        return object() if name in present else None
    return _spec


def test_embedded_available_mlx_present(monkeypatch) -> None:
    # mlx_lm importable → (True, "mlx_lm"), preferred even if llama_cpp also present.
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec({"mlx_lm"}))
    assert L.embedded_available() == (True, "mlx_lm")


def test_embedded_available_mlx_preferred_over_llama(monkeypatch) -> None:
    monkeypatch.setattr(
        importlib.util, "find_spec", _fake_find_spec({"mlx_lm", "llama_cpp"})
    )
    assert L.embedded_available() == (True, "mlx_lm")


def test_embedded_available_llama_present(monkeypatch) -> None:
    # Only llama_cpp importable → (True, "llama_cpp").
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec({"llama_cpp"}))
    assert L.embedded_available() == (True, "llama_cpp")


def test_embedded_available_neither_present(monkeypatch) -> None:
    # Neither engine importable → (False, <pip install hint>), never raises.
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec(set()))
    ok, detail = L.embedded_available()
    assert ok is False
    assert "ronin-cli[local]" in detail
    assert "pip install" in detail


def test_embedded_available_handles_find_spec_raising(monkeypatch) -> None:
    # A broken/namespace package can make find_spec raise — treat as not importable,
    # don't propagate, fall through to the install hint.
    def _raises(name: str, *args, **kwargs):  # noqa: ANN002, ANN003
        raise ValueError("broken package")
    monkeypatch.setattr(importlib.util, "find_spec", _raises)
    ok, detail = L.embedded_available()
    assert ok is False
    assert "ronin-cli[local]" in detail


# --------------------------------------------------------------------------- #
# apply_embedded_config — persists provider="local" via the save_config seam
# --------------------------------------------------------------------------- #
def _isolate_config_dirs(monkeypatch, tmp_path):
    """Point both ronin config-dir globals at a fresh tmp dir so load_config /
    save_config never see the developer's real ~/.config/ronin or a stray .ronin/
    in cwd. Also drops env keys that load_config would inject. Returns the user dir.
    """
    from ronin_cli import config as C

    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"  # deliberately absent → find_config_path skips it
    monkeypatch.setattr(C, "USER_DIR", user_dir)
    monkeypatch.setattr(C, "PROJECT_DIR", project_dir)
    monkeypatch.setattr(C, "LEGACY_USER_DIR", tmp_path / "legacy_user")
    monkeypatch.setattr(C, "LEGACY_PROJECT_DIR", tmp_path / "legacy_project")
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(env, raising=False)
    return C, user_dir


def test_apply_embedded_config_writes_provider_local(monkeypatch, tmp_path) -> None:
    # The real save_config seam must persist provider="local" to disk under the
    # monkeypatched user dir — read it back to prove it landed.
    import tomllib

    C, user_dir = _isolate_config_dirs(monkeypatch, tmp_path)
    path = L.apply_embedded_config("mlx-community/Qwen2.5-Coder-7B-Instruct-4bit")

    assert path == user_dir / C.CONFIG_FILENAME
    assert path.exists()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    assert data["provider"] == "local"
    assert data["model"] == "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    # demo_mode False + base_url "" are dropped by save_config's falsy-prune, so the
    # config is keyless/in-process by absence, never anthropic-by-default.
    assert "base_url" not in data
    assert data.get("demo_mode") in (None, False)


def test_apply_embedded_config_empty_model_auto_picks(monkeypatch, tmp_path) -> None:
    # Empty model → cfg.model becomes None, which save_config prunes; EmbeddedProvider
    # then auto-picks by RAM on first use. provider="local" must still be written.
    import tomllib

    C, user_dir = _isolate_config_dirs(monkeypatch, tmp_path)
    path = L.apply_embedded_config("")

    with path.open("rb") as fh:
        data = tomllib.load(fh)
    assert data["provider"] == "local"
    assert "model" not in data  # None → pruned → EmbeddedProvider auto-picks


def test_apply_embedded_config_preserves_other_settings(monkeypatch, tmp_path) -> None:
    # The load → mutate → save seam must keep unrelated settings (e.g. an API key)
    # rather than clobbering the whole config.
    import tomllib

    C, user_dir = _isolate_config_dirs(monkeypatch, tmp_path)
    seed = C.RoninConfig(provider="anthropic", anthropic_api_key="sk-keep-me")
    C.save_config(seed, scope="user")

    L.apply_embedded_config("local")

    with (user_dir / C.CONFIG_FILENAME).open("rb") as fh:
        data = tomllib.load(fh)
    assert data["provider"] == "local"
    assert data.get("anthropic_api_key") == "sk-keep-me"
