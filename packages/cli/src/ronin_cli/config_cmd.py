"""Helpers for the `ronin config` command — view and set core settings.

Only a small, safe whitelist of agent-facing settings is editable here (routing,
budget, the sentinel toggle, provider/model). Value coercion (str → bool/float/
None) is pure and unit-tested; the command wraps it with load/save.
"""
from __future__ import annotations

# field → (type, human help). Only these can be set via `ronin config --set`.
SETTABLE: dict[str, tuple[str, str]] = {
    "provider": ("str", "default LLM provider"),
    "model": ("str", "default model (or '-' to clear)"),
    "route_fast": ("str", "cheap/free blade for simple turns (provider:model)"),
    "route_strong": ("str", "strong blade for complex turns (provider:model)"),
    "budget": ("float", "session spend cap in USD (or '-' to clear)"),
    "sentinel": ("bool", "abstain-over-bluff mode (true/false)"),
    "faithfulness": ("enum", "grounding harness mode (off/warn/gate)"),
    "interaction_style": ("enum", "interactive delivery (balanced/direct/supportive/quiet)"),
    "relational_checkins": ("bool", "allow occasional task-relevant check-ins (true/false)"),
}

# Allowed values for "enum"-kind settings.
ENUM_CHOICES: dict[str, tuple[str, ...]] = {
    "faithfulness": ("off", "warn", "gate"),
    "interaction_style": ("balanced", "direct", "supportive", "quiet"),
}

_TRUE = {"true", "1", "yes", "on", "y"}
_FALSE = {"false", "0", "no", "off", "n"}


class BadSetting(ValueError):
    pass


def coerce_value(field: str, raw: str):
    """Coerce a string value for ``field`` to its typed value. '-' / '' clears
    (→ None). Raises BadSetting on an unknown field or unparseable value."""
    if field not in SETTABLE:
        raise BadSetting(f"unknown setting {field!r} — settable: {', '.join(sorted(SETTABLE))}")
    kind = SETTABLE[field][0]
    raw = raw.strip()
    if kind == "enum":
        choices = ENUM_CHOICES[field]
        low = raw.lower()
        if low not in choices:
            raise BadSetting(f"{field}: expected one of {', '.join(choices)}, got {raw!r}")
        return low
    if raw in ("-", "") and kind != "bool":
        return None
    if kind == "bool":
        low = raw.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise BadSetting(f"{field}: expected true/false, got {raw!r}")
    if kind == "float":
        try:
            return float(raw)
        except ValueError:
            raise BadSetting(f"{field}: expected a number, got {raw!r}")
    return raw  # str


def failover_specs(spec_str: str) -> list[dict]:
    """Parse 'groq,gemini,openrouter:qwen/...' → an ordered failover spec list.
    Each entry is a provider[:model] dict. Pure."""
    out: list[dict] = []
    for part in (spec_str or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            provider, _, model = part.partition(":")
            out.append({"provider": provider.strip(), "model": model.strip()})
        else:
            out.append({"provider": part})
    return out


def parse_set(arg: str) -> tuple[str, str]:
    """Split a 'field=value' (or 'field value') assignment. Raises BadSetting."""
    if "=" in arg:
        field, _, value = arg.partition("=")
    else:
        parts = arg.split(None, 1)
        if len(parts) != 2:
            raise BadSetting("use --set field=value (e.g. --set budget=0.50)")
        field, value = parts
    return field.strip(), value.strip()
