"""ronin's EMBEDDED, in-process LLM brain — run FREE, OFFLINE, and KEYLESS.

This is the most local ronin can get: no API key, no Ollama daemon, no server,
no network round-trip per turn. The model runs *inside* the ronin process via a
small in-process inference engine and a 4-bit-quantized open coder model.

The honest trade-offs (read before relying on it):

  * **First use downloads weights (~GBs).** On the very first ``complete()`` the
    engine pulls a quantized model from the HuggingFace Hub (no key needed) into
    ``~/.ronin/models/`` (honoring ``RONIN_HOME``). That is a one-time, multi-GB
    download that BLOCKS the first call; every later run memory-maps the cache and
    starts fast. Surface a "downloading…" notice around the first turn.
  * **Slower + weaker than cloud Claude.** A 1.5B–7B local coder model is far below
    Claude/GPT class on multi-file agentic reasoning. Great for offline edits,
    autocomplete-grade help, and no-key demos; unreliable for hard agentic work.
  * **Needs the optional ``[local]`` extra.** The heavy engine is NOT a core
    dependency. This module imports cleanly with the engine absent — the import of
    ``ronin_cli`` never requires it — and only raises a clear, actionable error at
    *call* time if the engine is missing:  ``pip install ronin-cli[local]``.

Engine choice (decided per machine, see ``select_engine``):
  * **mlx-lm** — primary on Apple Silicon. Pure-Python wheels + the prebuilt Metal
    ``mlx`` wheel: zero C++/CMake/Xcode build, Metal + unified-memory GPU by
    default. ``pip install mlx-lm``.
  * **llama-cpp-python** — cross-platform fallback (Linux/Windows/CUDA, Intel Mac).
    Runs GGUF in-process; prebuilt Metal wheels may not cover the newest Python, so
    a plain install can compile from source.

Layered for testability — the PURE pieces below take NO I/O and are unit-tested
without ever loading a model or touching the network:
  * ``select_engine``       — pick "mlx" vs "llama_cpp" from machine + sys.platform.
  * ``recommend_embedded_model`` — RAM → (HF repo id) for the chosen engine.
  * ``model_cache_dir``     — the ``~/.ronin/models/`` cache path (honors RONIN_HOME).
  * ``messages_to_chat``    — neutral ``Message`` list → role/content dicts for the
    tokenizer's chat template.
  * ``MissingEngineError``  / ``install_hint`` — the clear missing-engine message.

The IMPURE part (``EmbeddedProvider.complete``) lazily imports the engine, loads +
caches the model on first use, and runs generation in-process.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import PrivateAttr

# These are intentionally imported at module top — they are LIGHT (ronin's own
# agent-patterns types), so the module stays importable with the engine absent.
from ronin_agent_patterns.providers.base import LLMProvider, LLMResponse, Message
from ronin_agent_patterns.types import Tool

Engine = Literal["mlx", "llama_cpp"]


# --------------------------------------------------------------------------- #
# Errors / install guidance
# --------------------------------------------------------------------------- #
def install_hint(engine: Engine | None = None) -> str:
    """The exact command to install the embedded engine. Defaults to the umbrella
    extra (``ronin-cli[local]``) which pulls the right engine per platform; pass an
    explicit ``engine`` to get its direct pip line."""
    if engine == "mlx":
        return "pip install mlx-lm   (or: pip install ronin-cli[local])"
    if engine == "llama_cpp":
        return (
            "pip install llama-cpp-python "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/metal"
            "   (or: pip install ronin-cli[local])"
        )
    return "pip install ronin-cli[local]"


class MissingEngineError(RuntimeError):
    """Raised at CALL time (never import time) when the embedded engine isn't
    installed. Message tells the user exactly what to run."""

    def __init__(self, engine: Engine, original: Exception | None = None) -> None:
        self.engine = engine
        self.original = original
        pkg = "mlx-lm" if engine == "mlx" else "llama-cpp-python"
        msg = (
            f"ronin's embedded local model needs the '{pkg}' engine, which isn't "
            f"installed. Run:  {install_hint(engine)}\n"
            "(One-time install; then the model auto-downloads on first use — free, "
            "offline, no API key.)"
        )
        super().__init__(msg)


# --------------------------------------------------------------------------- #
# PURE: engine selection
# --------------------------------------------------------------------------- #
def select_engine(
    machine: str | None = None, sys_platform: str | None = None
) -> Engine:
    """Pick the in-process engine for this machine. PURE — args injectable for tests.

    Apple Silicon (``arm64`` on ``darwin``) → ``"mlx"`` (Metal-native, zero-build,
    fastest + lowest-RAM decode). Everything else → ``"llama_cpp"`` (GGUF,
    cross-platform: Linux/Windows/CUDA and Intel Macs). Defaults read the live
    machine via ``platform.machine()`` / ``sys.platform``.
    """
    mach = (machine if machine is not None else platform.machine()).lower()
    plat = (sys_platform if sys_platform is not None else sys.platform).lower()
    if plat == "darwin" and mach in ("arm64", "aarch64"):
        return "mlx"
    return "llama_cpp"


# --------------------------------------------------------------------------- #
# PURE: model selection by RAM
# --------------------------------------------------------------------------- #
# RAM (GB) → embedded coder model, per engine. We mirror ``local_setup``'s
# qwen2.5-coder ladder and RAM floors, but reference the *quantized* repos these
# in-process engines actually load:
#   * MLX  → mlx-community 4-bit safetensors repos (Apple-Silicon-only).
#   * GGUF → Qwen first-party q4_k_m GGUF repos (cross-platform).
# LICENSE NOTE: 1.5B / 7B / 14B are Apache-2.0 (safe to auto-fetch). The 3B size is
# Qwen RESEARCH (non-commercial) — deliberately NOT in this ladder, skipping 3B
# entirely. Tiers are CONSERVATIVE for an EMBEDDED (in-process) brain, where ronin
# and the model share RAM: a 7B-4bit needs ~6-7GB resident, so it's gated to >=16GB
# (an 8GB Mac runs the 1.5B — the 7B would swap/OOM there).
#
#     detected RAM (GB)   MLX repo                                         GGUF repo
#     ----------------    ------------------------------------------       --------------------------------
#     < 16                mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit   Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF
#     16 ..< 32           mlx-community/Qwen2.5-Coder-7B-Instruct-4bit     Qwen/Qwen2.5-Coder-7B-Instruct-GGUF
#     >= 32               mlx-community/Qwen2.5-Coder-14B-Instruct-4bit    Qwen/Qwen2.5-Coder-14B-Instruct-GGUF
#
# (We cap the embedded default at 14B even on big boxes — a 32B in-process model is
# heavy to load and the quality jump rarely justifies the RAM for an *embedded*
# brain; users who want bigger can still point ronin at Ollama via `ronin local`.)
_MLX_RAM_TABLE: tuple[tuple[int, str], ...] = (
    (16, "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"),
    (32, "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"),
)
_MLX_LARGEST = "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"

_GGUF_RAM_TABLE: tuple[tuple[int, str], ...] = (
    (16, "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"),
    (32, "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"),
)
_GGUF_LARGEST = "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF"

# For the GGUF engine we also need the specific quant FILE inside the repo. q4_k_m
# is the quality/size sweet spot. Keyed by repo id.
_GGUF_FILES: dict[str, str] = {
    "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
    "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF": "qwen2.5-coder-14b-instruct-q4_k_m.gguf",
}


def recommend_embedded_model(ram_gb: int, engine: Engine) -> str:
    """Pick the HuggingFace repo id for the embedded model on a machine with
    ``ram_gb`` GB of RAM, for the chosen ``engine``. PURE — no I/O.

        <16 → 1.5B · <32 → 7B · else → 14B  (4-bit / q4_k_m quant)

    Non-positive / unknown RAM falls back to the smallest model (safe default for an
    unreadable machine). The returned id is what the engine auto-downloads on first
    use. For GGUF, pair this with :func:`gguf_filename_for` to get the quant file.
    """
    table = _MLX_RAM_TABLE if engine == "mlx" else _GGUF_RAM_TABLE
    largest = _MLX_LARGEST if engine == "mlx" else _GGUF_LARGEST
    if ram_gb <= 0:
        return table[0][1]
    for ceiling, repo in table:
        if ram_gb < ceiling:
            return repo
    return largest


def gguf_filename_for(repo_id: str) -> str:
    """The q4_k_m GGUF filename to load inside a Qwen GGUF ``repo_id`` (used only by
    the llama-cpp engine; ``Llama.from_pretrained`` needs both repo + filename).
    Falls back to a ``*q4_k_m.gguf`` glob if the repo isn't in the known table."""
    return _GGUF_FILES.get(repo_id, "*q4_k_m.gguf")


# --------------------------------------------------------------------------- #
# PURE: cache path (honors RONIN_HOME)
# --------------------------------------------------------------------------- #
def ronin_home() -> Path:
    """ronin's home dir — ``$RONIN_HOME`` or ``~/.ronin`` — matching bushido /
    background / the run store. PURE (just env + path math)."""
    home = os.environ.get("RONIN_HOME")
    return Path(home) if home else Path.home() / ".ronin"


def model_cache_dir() -> Path:
    """Where embedded weights are cached: ``$RONIN_HOME/models`` (default
    ``~/.ronin/models``). The engine downloads here on first use and memory-maps it
    thereafter. PURE — does NOT create the dir (the impure load path does that)."""
    return ronin_home() / "models"


# --------------------------------------------------------------------------- #
# PURE: message → chat-template input
# --------------------------------------------------------------------------- #
def messages_to_chat(system: str, messages: list[Message]) -> list[dict[str, str]]:
    """Flatten ronin's neutral ``Message`` list into the ``[{role, content}, ...]``
    shape every coder tokenizer's chat template expects. PURE — no model, no I/O.

    * A non-empty ``system`` becomes the leading ``{"role": "system", ...}`` turn.
    * ``user`` / ``assistant`` turns pass through with their text.
    * ``tool`` result turns are folded into a ``user`` turn (these small local coder
      models don't reliably consume a separate ``tool`` role; presenting the result
      as user text is the robust degrade), prefixed so the model sees it's a result.
    * Empty-content turns are skipped (the template would otherwise emit blank turns).
    """
    chat: list[dict[str, str]] = []
    if system and system.strip():
        chat.append({"role": "system", "content": system})
    for m in messages:
        if m.role == "tool":
            label = f"[tool result{f' for {m.name}' if m.name else ''}]"
            text = f"{label}\n{m.content}".strip()
            if text:
                chat.append({"role": "user", "content": text})
            continue
        content = m.content or ""
        # An assistant turn that was pure tool-calls (no text) carries no content
        # the local model can use — skip it rather than emit an empty assistant turn.
        if not content.strip():
            continue
        chat.append({"role": m.role, "content": content})
    return chat


def render_prompt(tokenizer: Any, chat: list[dict[str, str]]) -> str:
    """Apply the model's own chat template to ``chat`` (ChatML for Qwen-Coder),
    adding the generation prompt. Kept tiny + separate so the formatting seam is
    obvious; ``tokenizer`` is the loaded HF/MLX tokenizer (impure caller supplies
    it). Falls back to a plain ChatML render if the tokenizer has no template."""
    apply = getattr(tokenizer, "apply_chat_template", None)
    if apply is not None:
        return apply(chat, tokenize=False, add_generation_prompt=True)
    # Defensive fallback — emit ChatML by hand (Qwen-Coder's native format).
    parts = [f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>" for m in chat]
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# The provider — IMPURE generation lives here, behind lazy engine imports.
# --------------------------------------------------------------------------- #
class EmbeddedProvider(LLMProvider):
    """An in-process LLMProvider backed by a local 4-bit coder model — FREE, OFFLINE,
    KEYLESS. No API key, no daemon, no server. The heavy engine is imported lazily
    inside :meth:`complete`, so constructing this provider (and importing this
    module) never requires the ``[local]`` extra; a missing engine raises a clear
    ``MissingEngineError`` only when you actually try to generate.

    ``model`` may be left empty/``"local"`` — it's auto-selected from RAM + engine on
    first use. Pass an explicit HF repo id to pin one.

    Tool-calling: these small local models don't emit structured tool calls, so
    ``complete`` returns ``tool_calls=[]``; plain Q&A and edits work, tool loops
    degrade gracefully (they don't crash). ``usage`` is best-effort token counts.
    """

    model: str = ""  # "" / "local" → auto-pick by RAM; else an explicit HF repo id
    max_tokens_default: int = 1024

    # Loaded lazily on first complete() and cached for the process lifetime.
    _engine: Engine | None = PrivateAttr(default=None)
    _model_obj: Any = PrivateAttr(default=None)
    _tokenizer: Any = PrivateAttr(default=None)
    _resolved_repo: str | None = PrivateAttr(default=None)

    # ------------------------------------------------------------------ #
    # Resolution helpers (cheap; reused by complete()).
    # ------------------------------------------------------------------ #
    def _engine_choice(self) -> Engine:
        if self._engine is None:
            self._engine = select_engine()
        return self._engine

    def _resolve_repo(self) -> str:
        """The HF repo id to load: an explicit ``model`` (unless it's the generic
        ``"local"`` sentinel) wins; otherwise auto-pick from RAM + engine."""
        if self._resolved_repo is not None:
            return self._resolved_repo
        explicit = (self.model or "").strip()
        if explicit and explicit.lower() != "local":
            self._resolved_repo = explicit
        else:
            from .local_setup import detect_ram_gb  # reuse the RAM probe

            self._resolved_repo = recommend_embedded_model(
                detect_ram_gb(), self._engine_choice()
            )
        return self._resolved_repo

    # ------------------------------------------------------------------ #
    # Lazy model load — downloads on first use, cached under ~/.ronin/models.
    # ------------------------------------------------------------------ #
    def _ensure_loaded(self) -> None:
        if self._model_obj is not None:
            return
        engine = self._engine_choice()
        repo = self._resolve_repo()
        cache = model_cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        # Point HF's downloader at ronin's cache so weights land in ~/.ronin/models
        # (honors RONIN_HOME) instead of the global HF cache.
        os.environ.setdefault("HF_HOME", str(cache))

        if engine == "mlx":
            self._load_mlx(repo)
        else:
            self._load_llama_cpp(repo, cache)

    def _load_mlx(self, repo: str) -> None:
        try:
            from mlx_lm import load  # lazy: heavy + Apple-Silicon-only
        except ImportError as e:  # engine not installed
            raise MissingEngineError("mlx", e) from e
        # First call downloads + caches the 4-bit weights from the HF Hub (no key).
        self._model_obj, self._tokenizer = load(repo)

    def _load_llama_cpp(self, repo: str, cache: Path) -> None:
        try:
            from llama_cpp import Llama  # lazy: heavy, may compile from source
        except ImportError as e:
            raise MissingEngineError("llama_cpp", e) from e
        self._model_obj = Llama.from_pretrained(
            repo_id=repo,
            filename=gguf_filename_for(repo),
            n_gpu_layers=-1,   # offload all layers to Metal/CUDA when present
            n_ctx=8192,
            cache_dir=str(cache),
            verbose=False,
        )
        self._tokenizer = None  # llama-cpp applies the chat template internally

    # ------------------------------------------------------------------ #
    # The contract: complete(...) -> LLMResponse(.text). stream() is inherited
    # from the base class (it wraps complete()), so the streaming UI path works.
    # ------------------------------------------------------------------ #
    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self._ensure_loaded()
        chat = messages_to_chat(system, messages)
        engine = self._engine_choice()
        n = max_tokens or self.max_tokens_default

        if engine == "mlx":
            text, usage = self._generate_mlx(chat, n)
        else:
            text, usage = self._generate_llama_cpp(chat, n)

        # Small local models can emit ChatML control tokens verbatim — strip them
        # so callers (and the UI) get clean text.
        for _tok in ("<|im_end|>", "<|im_start|>", "<|endoftext|>"):
            text = text.replace(_tok, "")
        text = text.strip()

        return LLMResponse(
            text=text,
            tool_calls=[],          # small local models: no structured tool calls
            stop_reason="end_turn",
            usage=usage,
        )

    def _generate_mlx(self, chat: list[dict[str, str]], n: int) -> tuple[str, dict[str, int]]:
        from mlx_lm import generate  # lazy (already importable past _ensure_loaded)

        prompt = render_prompt(self._tokenizer, chat)
        text = generate(
            self._model_obj, self._tokenizer,
            prompt=prompt, max_tokens=n, verbose=False,
        )
        return text, {}

    def _generate_llama_cpp(self, chat: list[dict[str, str]], n: int) -> tuple[str, dict[str, int]]:
        out = self._model_obj.create_chat_completion(messages=chat, max_tokens=n)
        choice = (out.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content", "") or ""
        u = out.get("usage") or {}
        usage = {
            "input_tokens": int(u.get("prompt_tokens", 0)),
            "output_tokens": int(u.get("completion_tokens", 0)),
        }
        return text, usage
