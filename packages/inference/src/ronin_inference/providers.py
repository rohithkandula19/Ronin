"""Provider abstraction + a mock provider for local/dev/tests (no network)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderResult:
    text: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0


class Provider:
    """A routable model endpoint with capability + pricing metadata.

    price_*_per_mtok is None when unknown — the gateway treats None as
    'unknown', never as free.
    """

    def __init__(
        self,
        *,
        id: str,
        local: bool,
        capabilities: set[str],
        price_in_per_mtok: float | None,
        price_out_per_mtok: float | None,
        healthy: bool = True,
    ) -> None:
        self.id = id
        self.local = local
        self.capabilities = set(capabilities)
        self.price_in_per_mtok = price_in_per_mtok
        self.price_out_per_mtok = price_out_per_mtok
        self.healthy = healthy

    def estimate_cost(self, in_tokens: int, out_tokens: int) -> float | None:
        if self.price_in_per_mtok is None or self.price_out_per_mtok is None:
            return None  # unknown — never coerced to 0.0
        return (in_tokens / 1e6) * self.price_in_per_mtok + (out_tokens / 1e6) * self.price_out_per_mtok

    def complete(self, prompt: str) -> ProviderResult:  # pragma: no cover - real providers override
        raise NotImplementedError


@dataclass
class MockProvider(Provider):
    """Deterministic in-process provider for tests. No network, free by design."""

    calls: list[str] = field(default_factory=list)

    def __init__(self, *, id: str = "mock:local", local: bool = True,
                 capabilities: set[str] | None = None, fail: bool = False,
                 healthy: bool = True, price_in_per_mtok: float | None = 0.0,
                 price_out_per_mtok: float | None = 0.0) -> None:
        # `fail` = provider is healthy but raises at call time (a runtime failure
        # to exercise failover); `healthy=False` = filtered out during ranking.
        super().__init__(id=id, local=local,
                         capabilities=capabilities or {"text", "code", "tool_use"},
                         price_in_per_mtok=price_in_per_mtok,
                         price_out_per_mtok=price_out_per_mtok, healthy=healthy)
        self.calls = []
        self._fail = fail

    def complete(self, prompt: str) -> ProviderResult:
        self.calls.append(prompt)
        if self._fail:
            raise RuntimeError(f"provider {self.id} unavailable")
        out = f"[{self.id}] echo: {prompt[:40]}"
        return ProviderResult(text=out, input_tokens=max(1, len(prompt) // 4),
                              output_tokens=max(1, len(out) // 4))
