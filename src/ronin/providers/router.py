"""Roles, not models. Config maps ``main``/``plan``/``fast`` to whatever you own.

The rule this encodes: **never burn the main model on grep triage.** A subagent
summarizing forty search hits and a compaction pass rewriting history are both
mechanical work, and routing them to the same model that writes the code is how a
session costs ten times what it should while getting no better.

So the role is part of the *request*, decided by the caller's intent, and the model
is part of the *config*, decided once. ``Router.for_subagent()`` and
``Router.for_compaction()`` exist so those two callers cannot get it wrong by
forgetting an argument — they do not take a role at all.

Config is TOML because ``tomllib`` is in the standard library and the file is
hand-edited:

    [roles]
    main = "kimi-k2"
    plan = "deepseek-r1"
    fast = "qwen-coder-7b"

    [models.kimi-k2]
    provider = "moonshot"
    model = "kimi-k2-0711-preview"
    api_key_env = "MOONSHOT_API_KEY"

    [models.qwen-coder-7b]
    provider = "mlx"
    model = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    native_tools = false
"""
from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .base import ModelClient, Transport
from .resilience import FailoverClient, RetryingClient, RetryPolicy, Sleeper
from .types import Capabilities, ProviderError


def _default_retry() -> RetryPolicy:
    """The ladder a config gets when it says nothing about retrying."""
    return RetryPolicy()


class Role(StrEnum):
    """What a request is *for*, which is what decides the model.

    Three is the whole ladder on purpose. A fourth role is a fourth thing to
    configure and a fourth chance to route expensive work to an expensive model by
    accident; anything that does not fit is either thinking (``plan``) or
    mechanical (``fast``).
    """

    MAIN = "main"
    PLAN = "plan"
    FAST = "fast"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Everything needed to build one client, as data.

    ``api_key_env`` names an environment variable rather than holding a key: a
    config file with a secret in it ends up in a git history.
    """

    name: str
    provider: str
    model: str
    base_url: str = ""
    api_key_env: str = ""
    capabilities: Capabilities | None = None
    adapter_path: str = ""
    #: Per-million-token prices, for the ledger. Absent means "unpriced", which
    #: the ledger records as zero cost *and* flags — not as free.
    price_in: float = 0.0
    price_out: float = 0.0
    price_cache_read: float = 0.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ModelSpec.name is required")
        if not self.provider:
            raise ValueError(f"model {self.name!r} must name a provider")
        if not self.model:
            raise ValueError(f"model {self.name!r} must name a model id")

    def api_key(self, env: Mapping[str, str] | None = None) -> str:
        """The key from the environment, or empty for a local/keyless endpoint."""
        source = env if env is not None else os.environ
        return source.get(self.api_key_env, "") if self.api_key_env else ""

    @property
    def priced(self) -> bool:
        return self.price_in > 0 or self.price_out > 0


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """The role→model mapping plus the model definitions."""

    roles: Mapping[Role, str]
    models: Mapping[str, ModelSpec]
    #: Ordered backups per role. A role with fallbacks gets a ``FailoverClient``;
    #: a role without gets its single client, unwrapped. Opt-in on purpose —
    #: failing over silently to a different model is a surprise unless asked for.
    fallbacks: Mapping[Role, tuple[str, ...]] = field(default_factory=dict)
    #: How patiently to retry a transient failure. One policy for every role: a
    #: per-model ladder is more knobs than anyone has a reason to turn.
    retry: RetryPolicy = field(default_factory=lambda: _default_retry())

    def __post_init__(self) -> None:
        if Role.MAIN not in self.roles:
            raise ValueError("config must map the 'main' role — it is the default")
        for role, model_name in self.roles.items():
            if model_name not in self.models:
                raise ValueError(
                    f"role {role.value!r} points at model {model_name!r}, "
                    f"which is not defined (defined: {sorted(self.models)})"
                )
        for role, names in self.fallbacks.items():
            if role not in self.roles:
                raise ValueError(
                    f"fallbacks are declared for role {role.value!r}, which is not "
                    "mapped to a model — a backup for nothing is a typo"
                )
            for name in names:
                if name not in self.models:
                    raise ValueError(
                        f"fallback {name!r} for role {role.value!r} is not defined "
                        f"(defined: {sorted(self.models)})"
                    )
            if self.roles[role] in names:
                raise ValueError(
                    f"role {role.value!r} lists its own model {self.roles[role]!r} as "
                    "a fallback — retrying the same endpoint is what the retry ladder "
                    "is for, and failover is for a *different* one"
                )

    def chain_for(self, role: Role) -> tuple[ModelSpec, ...]:
        """The primary spec for ``role`` followed by its fallbacks, in order."""
        primary = self.spec_for(role)
        names = self.fallbacks.get(role, ())
        return (primary, *(self.models[name] for name in names))

    def spec_for(self, role: Role) -> ModelSpec:
        """The spec for ``role``, falling back to ``main``.

        Falling back rather than raising is deliberate: a config that forgets
        ``fast`` should route mechanical work to the main model and still *work*,
        because the alternative is a crash in the middle of a session. The
        fallback is visible in the ledger, where a `fast` row naming the main model
        is the signal to fix the config.
        """
        name = self.roles.get(role) or self.roles[Role.MAIN]
        return self.models[name]


def load_config(path: str | Path) -> RouterConfig:
    """Read a TOML config file. Raises with the offending key on bad input."""
    raw_path = Path(path)
    try:
        with raw_path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ProviderError(f"cannot read provider config {raw_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProviderError(f"provider config {raw_path} is not valid TOML: {exc}") from exc
    return parse_config(data)


def parse_config(data: Mapping[str, Any]) -> RouterConfig:
    """Build a :class:`RouterConfig` from already-parsed TOML."""
    raw_models = data.get("models")
    if not isinstance(raw_models, Mapping) or not raw_models:
        raise ProviderError("provider config has no [models] section")
    models: dict[str, ModelSpec] = {}
    for name, entry in raw_models.items():
        if not isinstance(entry, Mapping):
            raise ProviderError(f"[models.{name}] must be a table")
        models[str(name)] = _parse_model(str(name), entry)

    raw_roles = data.get("roles")
    if not isinstance(raw_roles, Mapping) or not raw_roles:
        raise ProviderError("provider config has no [roles] section")
    roles: dict[Role, str] = {}
    for key, value in raw_roles.items():
        try:
            role = Role(str(key))
        except ValueError as exc:
            raise ProviderError(
                f"unknown role {key!r} — roles are {[r.value for r in Role]}"
            ) from exc
        roles[role] = str(value)

    raw_fallbacks = data.get("fallbacks")
    fallbacks: dict[Role, tuple[str, ...]] = {}
    if isinstance(raw_fallbacks, Mapping):
        for key, value in raw_fallbacks.items():
            try:
                role = Role(str(key))
            except ValueError as exc:
                raise ProviderError(
                    f"unknown role {key!r} in [fallbacks] — roles are "
                    f"{[r.value for r in Role]}"
                ) from exc
            if isinstance(value, str):
                # A single backup written without brackets is what people type.
                fallbacks[role] = (value,)
            elif isinstance(value, Sequence):
                fallbacks[role] = tuple(str(item) for item in value)
            else:
                raise ProviderError(
                    f"fallbacks for role {key!r} must be a model name or a list of them"
                )

    raw_retry = data.get("retry")
    retry = _default_retry()
    if isinstance(raw_retry, Mapping):
        retry = RetryPolicy(
            max_attempts=int(raw_retry.get("max_attempts", retry.max_attempts)),
            base_delay=float(raw_retry.get("base_delay", retry.base_delay)),
            max_delay=float(raw_retry.get("max_delay", retry.max_delay)),
            max_retry_after=float(raw_retry.get("max_retry_after", retry.max_retry_after)),
        )

    return RouterConfig(roles=roles, models=models, fallbacks=fallbacks, retry=retry)


def _parse_model(name: str, entry: Mapping[str, Any]) -> ModelSpec:
    caps = _parse_capabilities(entry)
    headers = entry.get("extra_headers")
    body = entry.get("extra_body")
    return ModelSpec(
        name=name,
        provider=str(entry.get("provider", "")),
        model=str(entry.get("model", "")),
        base_url=str(entry.get("base_url", "")),
        api_key_env=str(entry.get("api_key_env", "")),
        capabilities=caps,
        adapter_path=str(entry.get("adapter_path", "")),
        price_in=float(entry.get("price_in", 0.0)),
        price_out=float(entry.get("price_out", 0.0)),
        price_cache_read=float(entry.get("price_cache_read", 0.0)),
        extra_headers=dict(headers) if isinstance(headers, Mapping) else {},
        extra_body=dict(body) if isinstance(body, Mapping) else {},
    )


#: Capability keys a config may override. Anything else in the table is ignored
#: rather than fatal, so a config written for a newer Ronin still loads.
_CAP_KEYS = (
    "native_tools",
    "parallel_tools",
    "prompt_cache",
    "thinking",
    "vision",
)


def _parse_capabilities(entry: Mapping[str, Any]) -> Capabilities | None:
    """Capability overrides from config, or None to use the adapter's defaults.

    Partial overrides are supported and merged against the adapter default at
    build time — a config saying only ``native_tools = false`` should not have to
    restate ``max_context``.
    """
    present = [key for key in (*_CAP_KEYS, "max_context") if key in entry]
    if not present:
        return None
    return Capabilities(
        native_tools=bool(entry.get("native_tools", True)),
        parallel_tools=bool(entry.get("parallel_tools", entry.get("native_tools", True))),
        prompt_cache=bool(entry.get("prompt_cache", False)),
        thinking=bool(entry.get("thinking", False)),
        max_context=int(entry.get("max_context", 32_768)),
        vision=bool(entry.get("vision", False)),
    )


#: Builds a client from a spec. ``registry.build_client`` is the real one; tests
#: pass a stub so a router can be exercised without any adapter at all.
ClientFactory = Callable[..., ModelClient]


class Router:
    """Resolves a role to a live client, building each one at most once.

    Clients are cached because building one may load model weights (MLX) and
    because a per-request client would defeat connection reuse. The cache is keyed
    by model *name*, not by role, so two roles pointing at the same model share one
    client — which is also what makes "fast falls back to main" cheap.
    """

    def __init__(
        self,
        config: RouterConfig,
        *,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
        build: ClientFactory | None = None,
        sleep: Sleeper | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._env = env
        self._clients: dict[str, ModelClient] = {}
        # Keyed by role rather than by model name: two roles on the same primary
        # may have different fallback chains, so the wrapped client differs even
        # when the inner one is shared.
        self._chains: dict[Role, ModelClient] = {}
        # Injected for tests; the default is the real factory. Imported lazily to
        # keep the module import graph acyclic (registry imports router's types).
        self._build: ClientFactory | None = build
        # Injected so a six-attempt ladder is testable in milliseconds.
        self._sleep = sleep

    @property
    def config(self) -> RouterConfig:
        return self._config

    def spec_for(self, role: Role) -> ModelSpec:
        return self._config.spec_for(role)

    def client_for(self, role: Role) -> ModelClient:
        """The client for ``role``: retried, and failed over when configured.

        Every client is wrapped in :class:`~ronin.providers.resilience.RetryingClient`
        — retry is not opt-in, because a free-tier 429 is the normal case and a turn
        that dies on one is a turn the user has to restart by hand. Failover *is*
        opt-in, because silently answering from a different model is a surprise
        unless it was asked for.
        """
        cached = self._chains.get(role)
        if cached is not None:
            return cached

        chain = self._config.chain_for(role)
        members = [
            RetryingClient(self._build_one(spec), policy=self._config.retry, sleep=self._sleep)
            for spec in chain
        ]
        client: ModelClient
        if len(members) == 1:
            client = members[0]
        else:
            client = FailoverClient(members, labels=[spec.name for spec in chain])
        self._chains[role] = client
        return client

    def _build_one(self, spec: ModelSpec) -> ModelClient:
        """Build (or reuse) the raw client for one spec, before any wrapping."""
        cached = self._clients.get(spec.name)
        if cached is not None:
            return cached
        client = self._factory()(spec, transport=self._transport, env=self._env)
        self._clients[spec.name] = client
        return client

    def _factory(self) -> ClientFactory:
        if self._build is not None:
            return self._build
        from .registry import build_client

        return build_client

    # ------------------------------------------------------------------ #
    # The two callers that must never take the main model
    # ------------------------------------------------------------------ #

    def for_subagent(self) -> ModelClient:
        """The client a subagent gets. Takes no role argument, by design."""
        return self.client_for(Role.FAST)

    def for_compaction(self) -> ModelClient:
        """The client history compaction gets. Takes no role argument, by design."""
        return self.client_for(Role.FAST)
