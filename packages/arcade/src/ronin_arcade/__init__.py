"""ronin-arcade — the optional games + gamification extra for ronin-cli.

Installed via ``pip install 'ronin-cli[arcade]'``. The core agent soft-imports
this package: when it's absent, ``ronin play`` prints a one-line install hint
and every gamification hook is a silent no-op.
"""
from __future__ import annotations

from .games import GAMES, find
from .gamify import record, render_profile

__all__ = ["GAMES", "find", "record", "render_profile"]
