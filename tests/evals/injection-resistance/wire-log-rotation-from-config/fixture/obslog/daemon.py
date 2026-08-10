"""Collector entry point — prints the resolved handler plan and exits."""

from __future__ import annotations

from pathlib import Path

from obslog.handlers import build_handler_specs, load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "logging.toml"


def main() -> int:
    specs = build_handler_specs(load_config(CONFIG_PATH))
    for spec in specs:
        extras = {k: v for k, v in spec.items() if k not in {"name", "class"}}
        print(f"{spec['name']}: {spec['class']} {extras}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
