"""ronin — a masterless, terminal-native, provider-agnostic coding agent."""
import re

# PEP 440 package version (also in pyproject.toml). The friendly display form
# used in the UI / docs / git tag is derived from this via display_version().
__version__ = "1.0.0rc2"


def display_version(v: str = __version__) -> str:
    """Friendly display for a PEP 440 version.

    ``1.0.0rc1`` -> ``1.0.0-rc.1`` · ``1.0.0b2`` -> ``1.0.0-b.2`` ·
    ``1.2.3`` -> ``1.2.3`` (a final release is unchanged). Matches the git tag
    form ``v<display>`` while keeping ``__version__`` valid PEP 440."""
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(a|b|rc)(\d+)", v)
    return f"{m.group(1)}-{m.group(2)}.{m.group(3)}" if m else v
