"""Tests for the EMBEDDED in-process provider — PURE pieces ONLY.

NO real model load, NO network, NO weights download. Covers the unit-testable
seams of ``ronin_cli.embedded_provider``:
  * ``select_engine``            — machine/platform → "mlx" vs "llama_cpp".
  * ``recommend_embedded_model`` — RAM → HF repo id (per engine), incl. boundaries
    and the deliberate skip of the non-commercial 3B size.
  * ``gguf_filename_for``        — repo id → q4_k_m GGUF file (+ glob fallback).
  * ``model_cache_dir`` / ``ronin_home`` — ~/.ronin/models, honoring RONIN_HOME.
  * ``messages_to_chat``         — neutral Message list → chat-template dicts.
  * ``render_prompt``            — chat-template application (with a fake tokenizer).
  * the MISSING-ENGINE error path — via monkeypatching the lazy import to fail.
"""
from __future__ import annotations

import builtins

import pytest

from ronin_agent_patterns.providers.base import Message

from ronin_cli import embedded_provider as E


# --------------------------------------------------------------------------- #
# select_engine
# --------------------------------------------------------------------------- #
def test_select_engine_apple_silicon() -> None:
    assert E.select_engine(machine="arm64", sys_platform="darwin") == "mlx"
    assert E.select_engine(machine="aarch64", sys_platform="darwin") == "mlx"


def test_select_engine_cross_platform() -> None:
    assert E.select_engine(machine="x86_64", sys_platform="linux") == "llama_cpp"
    assert E.select_engine(machine="AMD64", sys_platform="win32") == "llama_cpp"
    # Intel Mac is NOT mlx-eligible.
    assert E.select_engine(machine="x86_64", sys_platform="darwin") == "llama_cpp"


# --------------------------------------------------------------------------- #
# recommend_embedded_model — MLX ladder
# --------------------------------------------------------------------------- #
def test_recommend_mlx_low_ram() -> None:
    assert E.recommend_embedded_model(4, "mlx") == "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
    assert E.recommend_embedded_model(7, "mlx") == "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"


def test_recommend_mlx_mid_ram() -> None:
    assert E.recommend_embedded_model(8, "mlx") == "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    assert E.recommend_embedded_model(15, "mlx") == "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"


def test_recommend_mlx_high_ram() -> None:
    assert E.recommend_embedded_model(16, "mlx") == "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"
    assert E.recommend_embedded_model(64, "mlx") == "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"


def test_recommend_mlx_unknown_ram_safe_default() -> None:
    # 0 / negative RAM → smallest model (safe on an unreadable machine).
    assert E.recommend_embedded_model(0, "mlx") == "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
    assert E.recommend_embedded_model(-5, "mlx") == "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"


# --------------------------------------------------------------------------- #
# recommend_embedded_model — GGUF ladder
# --------------------------------------------------------------------------- #
def test_recommend_gguf_ladder() -> None:
    assert E.recommend_embedded_model(4, "llama_cpp") == "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
    assert E.recommend_embedded_model(8, "llama_cpp") == "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
    assert E.recommend_embedded_model(16, "llama_cpp") == "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF"
    assert E.recommend_embedded_model(0, "llama_cpp") == "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"


def test_no_3b_in_either_ladder() -> None:
    # The 3B size is Qwen RESEARCH (non-commercial) and must NEVER be auto-fetched.
    for ram in range(0, 64):
        for engine in ("mlx", "llama_cpp"):
            assert "3B" not in E.recommend_embedded_model(ram, engine)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# gguf_filename_for
# --------------------------------------------------------------------------- #
def test_gguf_filename_known_repos() -> None:
    assert E.gguf_filename_for("Qwen/Qwen2.5-Coder-7B-Instruct-GGUF") == "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    assert E.gguf_filename_for("Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF") == "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"


def test_gguf_filename_unknown_repo_globs() -> None:
    assert E.gguf_filename_for("Some/Unknown-GGUF") == "*q4_k_m.gguf"


# --------------------------------------------------------------------------- #
# cache path / RONIN_HOME
# --------------------------------------------------------------------------- #
def test_model_cache_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RONIN_HOME", raising=False)
    expected = E.Path.home() / ".ronin" / "models"
    assert E.model_cache_dir() == expected


def test_model_cache_dir_honors_ronin_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    assert E.ronin_home() == tmp_path
    assert E.model_cache_dir() == tmp_path / "models"


# --------------------------------------------------------------------------- #
# messages_to_chat
# --------------------------------------------------------------------------- #
def test_messages_to_chat_basic_with_system() -> None:
    chat = E.messages_to_chat(
        "You are ronin.",
        [
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
            Message(role="user", content="reverse a list"),
        ],
    )
    assert chat == [
        {"role": "system", "content": "You are ronin."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "reverse a list"},
    ]


def test_messages_to_chat_empty_system_omitted() -> None:
    chat = E.messages_to_chat("", [Message(role="user", content="hi")])
    assert chat == [{"role": "user", "content": "hi"}]
    # Whitespace-only system is also dropped.
    chat2 = E.messages_to_chat("   ", [Message(role="user", content="hi")])
    assert chat2 == [{"role": "user", "content": "hi"}]


def test_messages_to_chat_tool_result_folds_into_user() -> None:
    chat = E.messages_to_chat(
        "",
        [
            Message(role="user", content="run it"),
            Message(role="tool", name="bash", content="exit 0", tool_call_id="t1"),
        ],
    )
    assert chat[0] == {"role": "user", "content": "run it"}
    assert chat[1]["role"] == "user"
    assert "tool result" in chat[1]["content"]
    assert "bash" in chat[1]["content"]
    assert "exit 0" in chat[1]["content"]


def test_messages_to_chat_skips_empty_assistant_turn() -> None:
    # A pure tool-call assistant turn (no text) carries nothing usable → skipped.
    chat = E.messages_to_chat(
        "",
        [
            Message(role="user", content="go"),
            Message(role="assistant", content=""),
            Message(role="user", content="again"),
        ],
    )
    assert chat == [
        {"role": "user", "content": "go"},
        {"role": "user", "content": "again"},
    ]


# --------------------------------------------------------------------------- #
# render_prompt — exercises the chat-template seam with a fake tokenizer
# --------------------------------------------------------------------------- #
class _FakeTokenizer:
    def apply_chat_template(self, chat, tokenize=False, add_generation_prompt=True):
        assert tokenize is False
        assert add_generation_prompt is True
        return "TEMPLATED:" + "|".join(f"{m['role']}={m['content']}" for m in chat)


def test_render_prompt_uses_tokenizer_template() -> None:
    chat = [{"role": "user", "content": "hi"}]
    out = E.render_prompt(_FakeTokenizer(), chat)
    assert out == "TEMPLATED:user=hi"


def test_render_prompt_chatml_fallback_without_template() -> None:
    class _NoTemplate:
        pass

    out = E.render_prompt(_NoTemplate(), [{"role": "user", "content": "hi"}])
    assert "<|im_start|>user" in out
    assert "hi" in out
    assert out.rstrip().endswith("<|im_start|>assistant")


# --------------------------------------------------------------------------- #
# install_hint
# --------------------------------------------------------------------------- #
def test_install_hint_strings() -> None:
    assert "ronin-cli[local]" in E.install_hint()
    assert "mlx-lm" in E.install_hint("mlx")
    assert "llama-cpp-python" in E.install_hint("llama_cpp")


# --------------------------------------------------------------------------- #
# MISSING-ENGINE error path — monkeypatch the lazy import to fail, no real load.
# --------------------------------------------------------------------------- #
def _force_import_error(monkeypatch: pytest.MonkeyPatch, blocked: str) -> None:
    """Make ``import <blocked>`` raise ImportError, leaving all other imports alone —
    simulates the engine package not being installed."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == blocked or name.startswith(blocked + "."):
            raise ImportError(f"No module named {blocked!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_missing_mlx_engine_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_import_error(monkeypatch, "mlx_lm")
    prov = E.EmbeddedProvider(model="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit")
    # Force the MLX path regardless of host machine.
    prov._engine = "mlx"
    with pytest.raises(E.MissingEngineError) as ei:
        prov.complete(
            system="", messages=[Message(role="user", content="hi")],
            tools=[], max_tokens=8,
        )
    msg = str(ei.value)
    assert "mlx-lm" in msg
    assert "ronin-cli[local]" in msg
    assert ei.value.engine == "mlx"


def test_missing_llama_cpp_engine_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_import_error(monkeypatch, "llama_cpp")
    prov = E.EmbeddedProvider(model="Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF")
    prov._engine = "llama_cpp"
    with pytest.raises(E.MissingEngineError) as ei:
        prov.complete(
            system="", messages=[Message(role="user", content="hi")],
            tools=[], max_tokens=8,
        )
    msg = str(ei.value)
    assert "llama-cpp-python" in msg
    assert "ronin-cli[local]" in msg
    assert ei.value.engine == "llama_cpp"


def test_module_imports_without_engine_installed() -> None:
    # The module is already imported at top; assert the heavy engines are NOT a
    # hard dependency of importing it (the provider class is constructible too).
    prov = E.EmbeddedProvider()
    assert prov.model == ""
    # No engine loaded just by constructing.
    assert prov._model_obj is None
