"""Reads the entry log and the project config, then renders a release."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from chg.config import build_config
from chg.render import render_release


def load_overrides(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    section = document.get("changelog", {})
    return dict(section) if isinstance(section, dict) else {}


def load_entries(path: Path) -> tuple[str, list[dict[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return document["version"], list(document["entries"])


def build_changelog(entries_path: Path, config_path: Path) -> str:
    version, entries = load_entries(entries_path)
    config = build_config(load_overrides(config_path))
    return render_release(version, entries, config)
