"""Smart model routing — spend the big model only when it's worth it.

When ``route_fast`` and ``route_strong`` are both configured, ronin classifies
each turn and routes simple ones (greetings, short lookups) to the cheap/fast
model and complex ones (coding, multi-step, file work) to the strong model.
A pragmatic, cost-aware default for shipping agents at a startup.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import RoninConfig

# Signals that a turn likely needs the strong model.
_COMPLEX_KW = (
    "code", "fix", "bug", "refactor", "implement", "debug", "edit", "rewrite",
    "create", "build", "test", "error", "function", "class", "migrate", "design",
    "investigate", "explain", "review", "optimi", "trace", "stack",
)


def classify(message: str) -> str:
    """Return ``"complex"`` or ``"simple"`` for ``message``."""
    m = message.lower().strip()
    if "@" in message or "```" in message or len(m) > 220:
        return "complex"           # file mention, code block, or a long ask
    if message.strip().startswith(("/", "~")) or "/" in message.split(" ")[0]:
        return "complex"           # a path (cd into a project)
    if any(k in m for k in _COMPLEX_KW):
        return "complex"
    return "simple"


def pick_model(config: RoninConfig, message: str) -> str | None:
    """The model to use for this turn, or ``None`` when routing is off."""
    if not (config.route_fast and config.route_strong):
        return None
    return config.route_strong if classify(message) == "complex" else config.route_fast


# --- cross-provider routing -------------------------------------------------
# A route target may be a bare model ("llama-3.3-70b", same provider) or a
# provider-qualified "provider:model" (e.g. "cerebras:gpt-oss-120b") so a simple
# turn can run on a *free* provider while complex turns hit the strong one.

@dataclass
class RouteDecision:
    """Where a turn should run."""
    tier: str            # "simple" | "complex"
    provider: str        # resolved provider for this turn
    model: str | None    # resolved model (None → provider default)
    free: bool           # is this turn free?


def _parse_target(target: str, default_provider: str) -> tuple[str, str | None]:
    """Split "provider:model" → (provider, model); a bare value keeps the
    current provider. A leading scheme like http(s):// is never a provider."""
    if ":" in target and not target.startswith(("http://", "https://")):
        provider, _, model = target.partition(":")
        return provider.strip(), (model.strip() or None)
    return default_provider, target.strip() or None


def route_decision(config: RoninConfig, message: str) -> RouteDecision | None:
    """Resolve the full routing decision for a turn, or ``None`` when routing is
    off. Honors provider-qualified targets so simple turns can go to a free
    provider entirely (not just a cheaper model on the same one)."""
    if not (config.route_fast and config.route_strong):
        return None
    from .cost import is_free
    tier = classify(message)
    target = config.route_strong if tier == "complex" else config.route_fast
    provider, model = _parse_target(target, config.provider)
    return RouteDecision(tier=tier, provider=provider, model=model,
                         free=is_free(provider, model))
