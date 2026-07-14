"""User-defined hooks — run a shell command on tool events (Claude Code's hooks).

Declare them in ``.ronin/hooks.json``:

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
import shlex
import subprocess
from pathlib import Path
from typing import Callable

_EDIT_TOOLS = {"write_file", "edit_file", "multi_edit"}


def hooks_config_path(root: str | Path = ".") -> Path:
    return Path(root) / ".ronin" / "hooks.json"


def _is_trusted(p: Path) -> bool:
    """Fail-closed: any error resolving trust means NOT trusted."""
    try:
        from .plugin_trust import is_trusted
        return is_trusted(p)
    except Exception:  # noqa: BLE001
        return False


def untrusted_present(root: str | Path = ".") -> bool:
    """True if a hooks.json exists but is not trusted — so its hooks won't fire and
    the caller should say so instead of the user's hooks silently vanishing."""
    p = hooks_config_path(root)
    return p.is_file() and not _is_trusted(p)


def load_hooks(root: str | Path = ".", *, require_trust: bool = True) -> list[dict]:
    """The hooks declared in ``.ronin/hooks.json`` — but ONLY if the user trusted it.

    A hook is a shell command run on tool events, so ``.ronin/hooks.json`` (which
    lives in the repo) is arbitrary code execution: a cloned repo shipping one runs
    its commands as soon as the agent edits a file. Untrusted configs yield no
    hooks. ``require_trust=False`` is for this module's own unit tests only.
    """
    p = hooks_config_path(root)
    if not p.is_file():
        return []
    if require_trust and not _is_trusted(p):
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
        # $FILE goes into a shell=True string, so a path with shell metacharacters
        # (a repo can contain a file literally named `x; rm -rf ~`) would inject a
        # command even into a trusted hook. Quote it so it is always ONE argument.
        safe_target = shlex.quote(target)
        for h in hooks:
            if h.get("event") != event:
                continue
            cmd = str(h["command"]).replace("$FILE", safe_target)
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
