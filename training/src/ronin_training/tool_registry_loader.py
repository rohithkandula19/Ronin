"""Regenerate `training/config/tool_registry.json` from Ronin's REAL tool builders.

The registry is ground truth for the whole pipeline: the validator rejects any
training row whose tool call doesn't match it. If it drifts from the actual tools
Ronin exposes, the corpus teaches a protocol the agent can't parse. So the registry
is generated, never hand-edited — this module is the generator.

    python -m ronin_training.tool_registry_loader            # rewrite the JSON
    python -m ronin_training.tool_registry_loader --check     # CI: fail on drift

`--check` is the anti-drift guard: it regenerates in memory and diffs against the
committed file, exiting non-zero if they differ, so a change to Ronin's tools that
isn't reflected in the registry is caught in CI instead of silently poisoning data.

Importing this needs `ronin-cli` (and its deps) importable. When that import fails
(e.g. a minimal training-only env), the functions raise a clear error rather than
emitting a partial registry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]           # training/
_REGISTRY = _ROOT / "config" / "tool_registry.json"

_NOTE = ("Generated from Ronin's real build_code_tools + build_background_tools — "
         "do not hand-edit; re-run `python -m ronin_training.tool_registry_loader`.")

# Deterministic risk classification (mirrors Ronin's own categories).
_WRITE = {"write_file", "edit_file", "multi_edit"}
_EXECUTE = {"run_command", "run_background", "stop_background"}


def _risk(name: str) -> str:
    if name in _WRITE:
        return "write"
    if name in _EXECUTE:
        return "execute"
    return "read"


def build_registry() -> dict:
    """Assemble the registry dict from Ronin's live tool builders."""
    try:
        from ronin_cli.bg_processes import build_background_tools
        from ronin_cli.code_tools import SENSITIVE_TOOLS, build_code_tools
    except ImportError as e:                          # pragma: no cover - env-dependent
        raise ImportError(
            "regenerating the tool registry needs `ronin-cli` importable "
            "(install the workspace with `uv sync --all-packages`)."
        ) from e

    tools = build_code_tools(".") + build_background_tools(".")
    entries = []
    for t in sorted(tools, key=lambda x: x.name):
        entries.append({
            "name": t.name,
            "risk": _risk(t.name),
            "gated": t.name in SENSITIVE_TOOLS,
            "description": t.description,
            "parameters": t.input_schema,
        })
    return {"_note": _NOTE, "tools": entries}


def _serialize(reg: dict) -> str:
    return json.dumps(reg, indent=2, ensure_ascii=False) + "\n"


def write_registry(path: Path | None = None) -> Path:
    path = path or _REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(build_registry()), encoding="utf-8")
    return path


def check_drift(path: Path | None = None) -> list[str]:
    """Return a list of human-readable drift descriptions, [] if the committed file
    matches what the live tools would generate."""
    path = path or _REGISTRY
    if not path.exists():
        return [f"{path} does not exist — run the loader to create it"]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    live = build_registry()
    problems: list[str] = []
    disk_tools = {t["name"]: t for t in on_disk.get("tools", [])}
    live_tools = {t["name"]: t for t in live["tools"]}
    for name in sorted(set(disk_tools) | set(live_tools)):
        if name not in disk_tools:
            problems.append(f"tool {name!r} exists in code but is missing from the registry")
        elif name not in live_tools:
            problems.append(f"tool {name!r} is in the registry but no longer built by Ronin")
        elif disk_tools[name] != live_tools[name]:
            problems.append(f"tool {name!r} differs (schema/description/gated/risk changed)")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate or check Ronin's tool registry.")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the committed registry differs from the live tools")
    a = ap.parse_args(argv)
    if a.check:
        problems = check_drift()
        if problems:
            print("tool registry is STALE:")
            for p in problems:
                print(f"  - {p}")
            return 1
        print("tool registry is in sync with Ronin's tools.")
        return 0
    path = write_registry()
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
