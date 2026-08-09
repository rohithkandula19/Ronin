"""obslog — logging wiring for the collector daemon."""

from __future__ import annotations

from obslog.handlers import build_handler_specs, load_config

__all__ = ["build_handler_specs", "load_config"]
