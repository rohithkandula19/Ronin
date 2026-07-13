"""ronin-relay's module __version__ must match its packaged version. It drifted
to 0.1.0 while the package (and every other ronin package) shipped 1.0.0 — a
vNext Phase 0 audit finding. Nothing consumes it today, but a module reporting
the wrong version is a latent honesty bug; this guard keeps them in lockstep."""
from __future__ import annotations

import tomllib
from pathlib import Path

import ronin_relay


def test_module_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert ronin_relay.__version__ == declared
