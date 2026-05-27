"""User-defined hooks — run a shell command on tool events (Claude Code's hooks).

Declare them in ``.csk/hooks.json``:

    {
      "hooks": [
        {"event": "post_edit", "command": "ruff format $FILE"},
        {"event": "post_edit", "command": "prettier --write $FILE"},
        {"event": "post_run",  "command": "echo done"}
      ]
    }

- ``post_edit`` fires after a successful write_file / edit_file / multi_edit;
  ``$FILE`` is replaced with the edited path.
- ``post_run`` fires after a successful run_command.

Hooks are opt-in (you create the file), run in the project root, and are
displayed as they fire. A failing or hanging hook can never break the agent.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

_EDIT_TOOLS = {"write_file", "edit_file", "multi_edit"}


def load_hooks(root: str | Path = ".") -> list[dict]:
    p = Path(root) / ".csk" / "hooks.json"
    if not p.is_file():
        return []
    try:
        hooks = json.loads(p.read_text(encoding="utf-8")).get("hooks", [])
        return [h for h in hooks if isinstance(h, dict) and h.get("command")]
    except (OSError, ValueError):
        return []


def _event_for(tool_name: str) -> str | None:
    if tool_name in _EDIT_TOOLS:
        return "post_edit"
    if tool_name == "run_command":
        return "post_run"
    return None


def build_after_tool(hooks: list[dict], root: str | Path, *, console=None) -> Callable[[str, dict, str, bool], None]:
    """An ``after_tool`` callback that fires matching hooks after a tool succeeds."""
    root_path = Path(root).resolve()

    def after_tool(name: str, args: dict, result: str, is_error: bool) -> None:
        if is_error:
            return
        event = _event_for(name)
        if event is None:
            return
        target = str(args.get("path", "")) if event == "post_edit" else ""
        for h in hooks:
            if h.get("event") != event:
                continue
            cmd = str(h["command"]).replace("$FILE", target)
            try:
                r = subprocess.run(cmd, shell=True, cwd=str(root_path),
                                   capture_output=True, text=True, timeout=120)
            except Exception as e:  # noqa: BLE001
                if console:
                    console.print(f"  [#6b7089]⎔ hook failed: {cmd} ({e})[/#6b7089]")
                continue
            if console:
                mark = "✓" if r.returncode == 0 else "✗"
                console.print(f"  [#6b7089]⎔ hook {mark} {cmd}[/#6b7089]")

    return after_tool
