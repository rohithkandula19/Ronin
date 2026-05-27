from __future__ import annotations

import json
from pathlib import Path

from ro_claude_kit_cli.hooks import build_after_tool, load_hooks


def test_load_hooks(tmp_path: Path) -> None:
    assert load_hooks(tmp_path) == []
    (tmp_path / ".csk").mkdir()
    (tmp_path / ".csk" / "hooks.json").write_text(json.dumps(
        {"hooks": [{"event": "post_edit", "command": "touch $FILE.formatted"}]}))
    hooks = load_hooks(tmp_path)
    assert hooks and hooks[0]["event"] == "post_edit"


def test_post_edit_hook_runs_on_successful_edit(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1")
    hooks = [{"event": "post_edit", "command": "cp $FILE $FILE.bak"}]
    after = build_after_tool(hooks, tmp_path)
    after("write_file", {"path": "a.py"}, "ok", False)
    assert (tmp_path / "a.py.bak").is_file()       # hook ran with $FILE substituted


def test_hook_skipped_on_error_and_wrong_tool(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1")
    hooks = [{"event": "post_edit", "command": "cp $FILE $FILE.bak"}]
    after = build_after_tool(hooks, tmp_path)
    after("write_file", {"path": "a.py"}, "err", True)   # is_error → skip
    after("read_file", {"path": "a.py"}, "ok", False)     # non-edit tool → skip
    assert not (tmp_path / "a.py.bak").exists()
