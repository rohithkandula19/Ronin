"""Generate a safe .env.example from a .env — `ronin env-example`.

Keeps the keys and comments, blanks the (secret) values — so you can commit a
template without leaking credentials. Pure parsing, unit-tested.
"""
from __future__ import annotations


def mask_env(text: str, *, keep_safe: bool = True) -> dict:
    """Return ``.env.example`` text (values blanked) + the key list. Comments and
    blank lines are preserved. When ``keep_safe`` is set, obviously-non-secret
    values (booleans, numbers, localhost-ish) are kept as helpful defaults. Pure."""
    keys: list[str] = []
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, value = line.partition("=")
        key_clean = key.strip().removeprefix("export ").strip()
        keys.append(key_clean)
        val = value.strip().strip('"').strip("'")
        if keep_safe and _is_safe_default(val):
            out.append(f"{key}={value.strip()}")
        else:
            out.append(f"{key}=")
    return {"text": "\n".join(out) + ("\n" if text.endswith("\n") else ""), "keys": keys}


def _is_safe_default(val: str) -> bool:
    if val == "" or val.lower() in ("true", "false", "yes", "no", "0", "1"):
        return True
    if val.replace(".", "", 1).isdigit():
        return True
    low = val.lower()
    return any(h in low for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1")) and len(val) < 40
