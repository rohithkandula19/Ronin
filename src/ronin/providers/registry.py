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
from .local_adapter import build_local_adapter
from .mlx_local import MLXClient
from .openai_compat import KNOWN_BASE_URLS, MoonshotClient, OpenAICompatClient
from .router import ModelSpec
from .shim import ShimClient
from .types import (
    Capabilities,
    ModelDelta,
    ModelRequest,
    ProviderError,
    apply_capability_overrides,
)

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
    return MoonshotClient(**kwargs)


def _build_mlx(spec: ModelSpec, transport: Transport, env: Mapping[str, str] | None) -> ModelClient:
    del transport, env  # local generation needs neither
    return MLXClient(
        model=spec.model,
        adapter_path=spec.adapter_path or None,
    )


def _build_local_adapter_entry(
    spec: ModelSpec, transport: Transport, env: Mapping[str, str] | None
) -> ModelClient:
    """The ``local-adapter`` provider: the fine-tuned adapter, served in-process.

    A thin forward to :func:`~ronin.providers.local_adapter.build_local_adapter`,
    which already has this exact signature — the indirection exists only so the
    ``ADAPTERS`` entry reads like its neighbours rather than reaching into another
    module's namespace.

    Returns the *unshimmed* client on purpose: :func:`build_client` applies the shim
    from capabilities, and a builder that shimmed itself would be shimmed twice —
    which emits the tool-call tags twice and parses neither.
    """
    return build_local_adapter(spec, transport, env)


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
    # The fine-tuned adapter, served in-process. Distinct from "mlx" because it
    # picks its own backend (mlx on Apple silicon, transformers elsewhere) and
    # carries the adapter path, whereas "mlx" is the generic Apple-silicon lane.
    "local-adapter": _build_local_adapter_entry,
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

    Config capability overrides are merged *here* too, and in this order for a
    reason: the adapter is built first so its own defaults are the thing being
    merged onto — no builder has to restate what a provider supports, and no copy
    of those defaults can drift from the class that owns them. The merge lands
    before the shim decision because ``native_tools = false`` in a config is a
    request to shim, and a decision made from pre-override capabilities would
    ignore it.
    """
    builder = ADAPTERS.get(spec.provider)
    if builder is None:
        raise ProviderError(
            f"unknown provider {spec.provider!r} for model {spec.name!r} — "
            f"known providers: {sorted(ADAPTERS)}"
        )
    client = builder(spec, transport or HttpTransport(), env)
    if spec.capability_overrides:
        merged = apply_capability_overrides(
            client.capabilities(), spec.capability_overrides, model=spec.name
        )
        # A builder that already merged (the local adapter does, because it is also
        # reachable without a registry) reports the merged value already; wrapping
        # it again would only add a layer.
        if merged != client.capabilities():
            client = with_capabilities(client, merged)
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
