"""``python -m ronin_training.adapter`` — validate the shipped adapter configs.

A separate entry point rather than ``python -m ronin_training.adapter.config``: that
form re-executes a module the package has already imported, which Python warns about
and which would run module-level code twice.
"""
from __future__ import annotations

from .config import main

if __name__ == "__main__":
    raise SystemExit(main())
