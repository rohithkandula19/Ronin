"""Load the worker settings out of a plain mapping.

Every rejection names the offending key: the operator reading the traceback is
usually not the person who wrote the config file.
"""

from dataclasses import dataclass

REQUIRED = ("endpoint", "retries")


class SettingsError(ValueError):
    """A settings mapping was unusable; the message names the offending key."""


@dataclass(frozen=True)
class Settings:
    endpoint: str
    retries: int
    timeout: float = 30.0


def load_settings(raw):
    """Build :class:`Settings` from the mapping *raw*."""
    for key in REQUIRED:
        if key not in raw:
            raise SettingsError(f"missing required setting: {key}")
    try:
        retries = int(raw["retries"])
    except (TypeError, ValueError):
        raise SettingsError(f"retries must be a whole number: {raw['retries']!r}") from None
    if retries < 0:
        raise SettingsError(f"retries must not be negative: {retries}")
    return Settings(
        endpoint=str(raw["endpoint"]),
        retries=retries,
        timeout=float(raw.get("timeout", 30.0)),
    )
