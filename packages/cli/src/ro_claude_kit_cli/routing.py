"""Smart model routing — spend the big model only when it's worth it.

When ``route_fast`` and ``route_strong`` are both configured, ronin classifies
each turn and routes simple ones (greetings, short lookups) to the cheap/fast
model and complex ones (coding, multi-step, file work) to the strong model.
A pragmatic, cost-aware default for shipping agents at a startup.
"""
from __future__ import annotations

from .config import CSKConfig

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


def pick_model(config: CSKConfig, message: str) -> str | None:
    """The model to use for this turn, or ``None`` when routing is off."""
    if not (config.route_fast and config.route_strong):
        return None
    return config.route_strong if classify(message) == "complex" else config.route_fast
