"""Config in, client out. The one place a provider name becomes a class.

This is the seam that makes the headline claim true: adding Kimi K2 or a local
Qwen is an entry in :data:`ADAPTERS` plus a stanza in a TOML file, and no call
site changes. It is also where the format shim is applied — automatically, from
:class:`~ronin.providers.types.Capabilities`, because a human deciding per-model
whether to wrap in a shim is a human who will eventually forget.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace

from .anthropic import AnthropicClient
from .base import HttpTransport, ModelClient, Transport
from .mlx_local import LOCAL_DEFAULTS, MLXClient
from .openai_compat import KNOWN_BASE_URLS, MoonshotClient, OpenAICompatClient
from .router import ModelSpec
from .shim import ShimClient
from .types import Capabilities, ModelDelta, ModelRequest, ProviderError

#: Provider name → builder. The keys are what appears in a config file.
Builder = Callable[[ModelSpec, Transport, Mapping[str, str] | None], ModelClient]


def _build_anthropic(
    spec: ModelSpec, transport: Transport, env: Mapping[str, str] | None
) -> ModelClient:
    return AnthropicClient(
        model=spec.model,
        transport=transport,
        api_key=spec.api_key(env),
        base_url=spec.base_url or "https://api.anthropic.com",
        capabilities=spec.capabilities,
        extra_headers=spec.extra_headers,
    )


def _build_openai_compat(
    spec: ModelSpec, transport: Transport, env: Mapping[str, str] | None
) -> ModelClient:
    base_url = spec.base_url or KNOWN_BASE_URLS.get(spec.provider, "")
    if not base_url:
        raise ProviderError(
            f"model {spec.name!r} uses provider {spec.provider!r} but sets no "
            "base_url, and there is no default for that name — an "
            "openai-compatible endpoint has to say where it is"
        )
    return OpenAICompatClient(
        model=spec.model,
        transport=transport,
        base_url=base_url,
        api_key=spec.api_key(env),
        capabilities=spec.capabilities,
        extra_headers=spec.extra_headers,
        extra_body=spec.extra_body,
    )


def _build_moonshot(
    spec: ModelSpec, transport: Transport, env: Mapping[str, str] | None
) -> ModelClient:
    kwargs = {
        "model": spec.model,
        "transport": transport,
        "api_key": spec.api_key(env),
        "extra_headers": spec.extra_headers,
        "extra_body": spec.extra_body,
    }
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    if spec.capabilities is not None:
        kwargs["capabilities"] = spec.capabilities
    return MoonshotClient(**kwargs)


def _build_mlx(
    spec: ModelSpec, transport: Transport, env: Mapping[str, str] | None
) -> ModelClient:
    del transport, env  # local generation needs neither
    return MLXClient(
        model=spec.model,
        adapter_path=spec.adapter_path or None,
        capabilities=spec.capabilities or LOCAL_DEFAULTS,
    )


#: Every provider Ronin can talk to. One entry per *wire protocol*, not per
#: vendor — which is why eight OpenAI-compatible hosts share a single line.
ADAPTERS: Mapping[str, Builder] = {
    "anthropic": _build_anthropic,
    "openai-compatible": _build_openai_compat,
    "openai": _build_openai_compat,
    "deepseek": _build_openai_compat,
    "together": _build_openai_compat,
    "groq": _build_openai_compat,
    "openrouter": _build_openai_compat,
    "ollama": _build_openai_compat,
    "lmstudio": _build_openai_compat,
    "vllm": _build_openai_compat,
    "llamacpp": _build_openai_compat,
    "moonshot": _build_moonshot,
    "kimi": _build_moonshot,
    "mlx": _build_mlx,
}


def build_client(
    spec: ModelSpec,
    *,
    transport: Transport | None = None,
    env: Mapping[str, str] | None = None,
) -> ModelClient:
    """Build the client ``spec`` describes, shimming it if it lacks native tools.

    The shim decision is made *here*, from capabilities, so no other code has to
    know that a model is weak. A caller gets a client that accepts tool specs and
    returns ``ToolUse`` blocks either way.
    """
    builder = ADAPTERS.get(spec.provider)
    if builder is None:
        raise ProviderError(
            f"unknown provider {spec.provider!r} for model {spec.name!r} — "
            f"known providers: {sorted(ADAPTERS)}"
        )
    client = builder(spec, transport or HttpTransport(), env)
    if not client.capabilities().native_tools:
        return ShimClient(client)
    return client


def describe(spec: ModelSpec, client: ModelClient) -> str:
    """One line describing how a role is wired. Used by the doctor/demo output."""
    caps = client.capabilities()
    flags = [
        "native-tools" if caps.native_tools else "shimmed-tools",
        "parallel" if caps.parallel_tools else "serial",
        "cache" if caps.prompt_cache else "no-cache",
        "thinking" if caps.thinking else "no-thinking",
    ]
    priced = "priced" if spec.priced else "unpriced"
    return (
        f"{spec.name} → {spec.provider}:{spec.model} "
        f"[{', '.join(flags)}, {caps.max_context:,} ctx, {priced}]"
    )


def with_capabilities(client: ModelClient, caps: Capabilities) -> ModelClient:
    """Override a built client's capabilities (used by tests and by `--force`).

    Kept explicit rather than mutating: capabilities are a frozen value, and a
    client whose capabilities change under it would break the shim decision that
    was already made from them.
    """
    if isinstance(client, ShimClient):
        raise ProviderError("cannot re-declare capabilities on an already-shimmed client")
    return _Overridden(client, replace(caps))


class _Overridden:
    """A client that reports different capabilities than the one it wraps."""

    def __init__(self, inner: ModelClient, caps: Capabilities) -> None:
        self._inner = inner
        self._caps = caps

    def capabilities(self) -> Capabilities:
        return self._caps

    def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        return self._inner.stream(req)
