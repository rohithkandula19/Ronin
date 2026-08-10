"""``ronin --model ronin-qwen-local``: the fine-tuned adapter, served offline.

This is the runtime half of phase 12. The adapter is a LoRA on
Qwen2.5-Coder-1.5B-Instruct trained to be *ronin-native* — to emit a tool call the
format shim can parse, to respect the approval gate, and to change its approach after
a failure. **It does not add coding knowledge**; it changes protocol behaviour, and
nothing here should be read as a claim about reasoning.

Two lanes, because the adapter is produced by two paths that already exist in this
repo and neither covers both platforms:

* ``mlx`` — ``mlx_lm`` over a 4-bit base on Apple silicon. Delegated wholesale to
  :class:`~ronin.providers.mlx_local.MLXClient`, which already solved the hard part
  (a blocking token loop bridged into async without starving the event loop).
* ``hf`` — ``transformers`` (+ ``peft`` when an adapter is set) for Linux, CUDA and
  CPU. This is the lane a free Colab/Kaggle T4 produces an adapter for.

Both heavy stacks are imported **inside the one function that needs them** and fail
with a named :class:`~ronin.providers.types.ProviderError` naming what to install.
That is not politeness: this package has zero hard dependencies, and an
``ImportError`` traceback out of an adapter is indistinguishable from a broken
install.

Everything except the generation call itself is pure and tested offline — the route,
the config, the prompt rendering, and the adapter-directory check. The check
deliberately **fails closed**: an adapter path that is set but missing or half-written
raises instead of quietly serving the bare base model, because a silently-base run
produces a plausible number that is not a measurement of the adapter.
"""
from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..core.types import Role as MessageRole
from .base import ModelClient, Transport
from .mlx_local import LOCAL_DEFAULTS, Generator, MLXClient
from .router import ModelSpec, Role, RouterConfig
from .shim import ShimClient
from .types import Capabilities, ModelDelta, ModelRequest, ProviderError

#: The model name a user types. One name, so the flag is memorable and so the
#: offline default does not depend on anyone having written a config file.
LOCAL_MODEL_NAME = "ronin-qwen-local"
#: The provider name in a config file's ``[models.x] provider = …``.
LOCAL_PROVIDER = "local-adapter"

#: Base weights per lane. Apache-2.0; the adapter inherits the base's licence.
BASE_MODEL_HF = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
BASE_MODEL_MLX = "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"

#: Where the adapter directory is read from when config does not say. Read through
#: an injected env mapping, never ``os.environ`` at import.
ADAPTER_PATH_ENV = "RONIN_ADAPTER"
#: Which lane to use, when config does not say.
BACKEND_ENV = "RONIN_LOCAL_BACKEND"


class Backend(StrEnum):
    """Which local runtime serves the weights."""

    MLX = "mlx"
    HF = "hf"


# --------------------------------------------------------------------------- #
# The adapter directory — checked, never assumed
# --------------------------------------------------------------------------- #

#: Either family of adapter weight file is accepted: ``mlx_lm.lora`` writes
#: ``adapters.safetensors``, ``peft`` writes ``adapter_model.safetensors`` (or the
#: older ``.bin``). Requiring one name would reject an adapter this repo's own
#: training path produces.
ADAPTER_WEIGHT_FILES = (
    "adapters.safetensors",
    "adapter_model.safetensors",
    "adapter_model.bin",
)
ADAPTER_CONFIG_FILE = "adapter_config.json"


def adapter_problems(path: str | Path) -> tuple[str, ...]:
    """Every reason ``path`` is not a usable adapter directory. Pure.

    Returns reasons rather than raising so the doctor can print all of them at once,
    and so the same check can be a warning in one place and fatal in another.
    """
    directory = Path(path)
    if not directory.exists():
        return (f"adapter directory {directory} does not exist",)
    if not directory.is_dir():
        return (f"adapter path {directory} is not a directory",)
    problems: list[str] = []
    if not (directory / ADAPTER_CONFIG_FILE).is_file():
        problems.append(f"{directory} has no {ADAPTER_CONFIG_FILE}")
    if not any((directory / name).is_file() for name in ADAPTER_WEIGHT_FILES):
        problems.append(
            f"{directory} has none of {list(ADAPTER_WEIGHT_FILES)} — an adapter "
            "directory with a config and no weights is a half-finished checkpoint"
        )
    return tuple(problems)


def describe_adapter(path: str | Path | None) -> str:
    """One line for the doctor: what the local model will actually serve."""
    if not path:
        return "base model, no adapter (nothing fine-tuned is being served)"
    problems = adapter_problems(path)
    if problems:
        return f"adapter {path}: UNUSABLE — {'; '.join(problems)}"
    return f"adapter {path}: valid"


# --------------------------------------------------------------------------- #
# Prompt rendering
# --------------------------------------------------------------------------- #

_CHATML_ROLES: Mapping[MessageRole, str] = {
    MessageRole.SYSTEM: "system",
    MessageRole.USER: "user",
    MessageRole.ASSISTANT: "assistant",
    MessageRole.TOOL: "user",
}


def render_chatml(req: ModelRequest) -> str:
    """Render a request in Qwen2.5's ChatML template.

    ``mlx_local.render_prompt`` is deliberately a plain ``Role: text`` rendering and
    warns that guessing a template is how a local model quietly answers a
    differently-shaped prompt than it was trained on. This is not a guess: the base
    model is pinned, ChatML is its documented template, and the adapter is trained
    through it. Rendering it here rather than reaching for the tokenizer's copy keeps
    the function pure and testable with no weights on disk.

    Tool results come back as a user turn wrapping ``<tool_response>``, which is what
    Qwen's own template does with them. Tool *calls* in the transcript are re-rendered
    in the shim's dialect so a multi-turn history looks the same going in as it did
    coming out — a model shown its own past calls in a different shape learns to
    distrust the format.
    """
    head = "\n\n".join(part for part in (req.system, req.prefix) if part)
    parts: list[str] = []
    if head:
        parts.append(f"<|im_start|>system\n{head}<|im_end|>")
    for message in req.messages:
        label = _CHATML_ROLES[message.role]
        chunks: list[str] = []
        if message.text:
            chunks.append(message.text)
        for use in message.tool_uses:
            chunks.append(_render_call(use.name, use.arguments))
        for result in message.tool_results:
            chunks.append(f"<tool_response>\n{result.content}\n</tool_response>")
        body = "\n".join(chunks)
        if body:
            parts.append(f"<|im_start|>{label}\n{body}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def _render_call(name: str, arguments: Mapping[str, Any]) -> str:
    """One tool call in the shim's dialect, imported from the shim so it cannot drift."""
    import json

    from .shim import CLOSE_TAG, OPEN_TAG

    payload = json.dumps({"name": name, "arguments": dict(arguments)}, sort_keys=True)
    return f"{OPEN_TAG}{payload}{CLOSE_TAG}"


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #

#: How many tokens of context the pinned base actually has. Not a claim about the
#: adapter's trained sequence length, which is shorter — the runtime's limit is the
#: model's, and the training length is a property of the corpus.
LOCAL_ADAPTER_DEFAULTS = replace(LOCAL_DEFAULTS, max_context=32_768)


class LocalAdapterClient:
    """Serves the fine-tuned adapter (or its bare base) from local weights.

    ``generate`` is the test seam and the escape hatch for a third runtime: when it
    is given, no ML stack is imported at all, which is how the whole route/config/
    prompt path is covered offline.
    """

    provider_name = LOCAL_PROVIDER

    def __init__(
        self,
        *,
        model: str = "",
        adapter_path: str = "",
        backend: Backend = Backend.MLX,
        capabilities: Capabilities | None = None,
        generate: Generator | None = None,
        chat_template: Callable[[ModelRequest], str] | None = None,
    ) -> None:
        self._backend = Backend(backend)
        self._model = model or (
            BASE_MODEL_MLX if self._backend is Backend.MLX else BASE_MODEL_HF
        )
        self._adapter_path = adapter_path
        self._capabilities = capabilities or LOCAL_ADAPTER_DEFAULTS
        self._chat_template = chat_template or render_chatml
        self._hf_loaded: tuple[Any, Any, Any] | None = None
        if adapter_path:
            problems = adapter_problems(adapter_path)
            if problems:
                # Fail closed, at construction. Serving the base model when the user
                # asked for the adapter produces a number that looks like a
                # measurement of the fine-tune and is not one.
                raise ProviderError(
                    "; ".join(problems)
                    + f" — set {ADAPTER_PATH_ENV} to a finished checkpoint, or unset "
                    "it to run the base model deliberately",
                    provider=self.provider_name,
                    retryable=False,
                )
        engine_generate = generate
        if engine_generate is None and self._backend is Backend.HF:
            engine_generate = self._hf_generate
        # MLXClient owns the blocking-generator-to-async bridge; duplicating it here
        # would be a second copy of the one piece of this file that is hard.
        self._engine = MLXClient(
            model=self._model,
            adapter_path=self._adapter_path or None,
            capabilities=self._capabilities,
            generate=engine_generate,
            chat_template=self._chat_template,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def backend(self) -> Backend:
        return self._backend

    @property
    def adapter_path(self) -> str:
        return self._adapter_path

    def capabilities(self) -> Capabilities:
        return self._capabilities

    def describe(self) -> str:
        """One line for ``ronin util models`` / the doctor."""
        return (
            f"{LOCAL_MODEL_NAME} → {self._backend.value}:{self._model} "
            f"[{describe_adapter(self._adapter_path or None)}]"
        )

    def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        """Generate locally. Not ``async def``: the return type is the iterator."""
        return self._engine.stream(req)

    # -- the hf lane -------------------------------------------------------- #

    def _load_hf(self) -> tuple[Any, Any, Any]:
        """Load ``transformers`` (+ ``peft``) and the weights. The only heavy import."""
        if self._hf_loaded is not None:
            return self._hf_loaded
        try:
            from transformers import (  # type: ignore[import-not-found]  # optional, no stubs
                AutoModelForCausalLM,
                AutoTokenizer,
                TextIteratorStreamer,
            )
        except ImportError as exc:
            raise ProviderError(
                "the local adapter's 'hf' backend needs transformers and torch "
                "(`uv pip install transformers torch`, plus `peft` when an adapter "
                f"is set); on Apple silicon use {BACKEND_ENV}=mlx instead, which "
                "needs mlx-lm and no torch",
                provider=self.provider_name,
                retryable=False,
            ) from exc
        tokenizer = AutoTokenizer.from_pretrained(self._model)
        model = AutoModelForCausalLM.from_pretrained(self._model, device_map="auto")
        if self._adapter_path:
            try:
                from peft import PeftModel  # type: ignore[import-not-found]  # optional, no stubs
            except ImportError as exc:
                raise ProviderError(
                    "serving a LoRA adapter on the 'hf' backend needs peft "
                    "(`uv pip install peft`); refusing to fall back to the base "
                    "model, which would look like a measurement of the adapter",
                    provider=self.provider_name,
                    retryable=False,
                ) from exc
            model = PeftModel.from_pretrained(model, self._adapter_path)
        self._hf_loaded = (model, tokenizer, TextIteratorStreamer)
        return self._hf_loaded

    def _hf_generate(self, prompt: str, max_tokens: int) -> Iterator[str]:
        """Yield tokens from the transformers lane.

        ``model.generate`` blocks, so it runs on its own thread and the streamer is
        the queue between them. This whole body needs real weights and cannot run in
        CI — everything around it is covered by injecting ``generate``.
        """
        model, tokenizer, streamer_cls = self._load_hf()
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        streamer = streamer_cls(tokenizer, skip_prompt=True, skip_special_tokens=True)
        worker = threading.Thread(
            target=model.generate,
            kwargs={
                **inputs,
                "streamer": streamer,
                "max_new_tokens": max_tokens,
                "do_sample": False,
            },
            daemon=True,
        )
        worker.start()
        try:
            for chunk in streamer:
                yield str(chunk)
        finally:
            worker.join(timeout=1.0)


def _conforms(client: LocalAdapterClient) -> ModelClient:
    """Static proof that this adapter satisfies the protocol."""
    return client


# --------------------------------------------------------------------------- #
# Config: what a router needs to reach it
# --------------------------------------------------------------------------- #


def resolve_backend(env: Mapping[str, str] | None = None) -> Backend:
    """The lane from an injected env mapping, defaulting to mlx.

    Defaults to mlx rather than probing the platform: a probe here would make the
    function untestable and would still be wrong on a Mac with no mlx-lm installed.
    An explicit wrong lane fails with a named error naming the other one.
    """
    raw = (env or {}).get(BACKEND_ENV, "").strip().lower()
    if not raw:
        return Backend.MLX
    try:
        return Backend(raw)
    except ValueError as exc:
        raise ProviderError(
            f"{BACKEND_ENV}={raw!r} is not a local backend; "
            f"known backends: {[b.value for b in Backend]}",
            provider=LOCAL_PROVIDER,
            retryable=False,
        ) from exc


def local_adapter_spec(
    *,
    name: str = LOCAL_MODEL_NAME,
    adapter_path: str = "",
    backend: Backend | None = None,
    env: Mapping[str, str] | None = None,
) -> ModelSpec:
    """The :class:`~ronin.providers.router.ModelSpec` for the local adapter.

    ``price_in``/``price_out`` stay zero and that is not a placeholder: local
    generation genuinely costs nothing, which is the whole point of the lane.
    """
    lane = backend if backend is not None else resolve_backend(env)
    path = adapter_path or (env or {}).get(ADAPTER_PATH_ENV, "")
    return ModelSpec(
        name=name,
        provider=LOCAL_PROVIDER,
        model=BASE_MODEL_MLX if lane is Backend.MLX else BASE_MODEL_HF,
        adapter_path=path,
        capabilities=LOCAL_ADAPTER_DEFAULTS,
        extra_body={"backend": lane.value},
    )


def build_local_adapter(
    spec: ModelSpec,
    transport: Transport | None = None,
    env: Mapping[str, str] | None = None,
) -> ModelClient:
    """Registry-shaped builder: one line in ``registry.ADAPTERS`` wires this in.

    Deliberately does **not** apply the format shim — ``registry.build_client``
    applies it from capabilities, and a builder that shimmed itself would be shimmed
    twice.
    """
    del transport  # local generation has no transport
    raw = spec.extra_body.get("backend")
    backend = (
        Backend(str(raw)) if isinstance(raw, str) and raw else resolve_backend(env)
    )
    return LocalAdapterClient(
        model=spec.model,
        adapter_path=spec.adapter_path or (env or {}).get(ADAPTER_PATH_ENV, ""),
        backend=backend,
        capabilities=spec.capabilities or LOCAL_ADAPTER_DEFAULTS,
    )


def build_local_client(
    spec: ModelSpec,
    *,
    transport: Transport | None = None,
    env: Mapping[str, str] | None = None,
) -> ModelClient:
    """Router-shaped factory: builds *and* shims, for use without a registry entry.

    Exists so the local lane is complete and testable today: ``Router(config,
    build=build_local_client)`` resolves every role to the local adapter with no edit
    to ``registry.py``. Once ``registry.ADAPTERS`` carries :func:`build_local_adapter`
    the normal path does the same thing, and this stays useful for a caller that
    wants only the local lane.
    """
    client = build_local_adapter(spec, transport, env)
    if not client.capabilities().native_tools:
        return ShimClient(client)
    return client


def local_router_config(
    *,
    adapter_path: str = "",
    backend: Backend | None = None,
    env: Mapping[str, str] | None = None,
) -> RouterConfig:
    """A whole-router config that points every role at the local adapter.

    All three roles, not just ``main``: the point of the offline lane is that it
    works with no API key at all, and a config that routes ``fast`` to a model the
    user does not have turns every subagent into an error.
    """
    spec = local_adapter_spec(adapter_path=adapter_path, backend=backend, env=env)
    return RouterConfig(
        roles={Role.MAIN: spec.name, Role.PLAN: spec.name, Role.FAST: spec.name},
        models={spec.name: spec},
    )


def resolve_model_flag(
    name: str, *, env: Mapping[str, str] | None = None
) -> RouterConfig | None:
    """``--model <name>`` → a router config, or ``None`` if it names something else.

    Returning ``None`` rather than raising is what lets the CLI try this first and
    fall through to its normal config lookup: the flag is not owned by this module,
    only one of its values is.
    """
    if name.strip() != LOCAL_MODEL_NAME:
        return None
    return local_router_config(env=env)
