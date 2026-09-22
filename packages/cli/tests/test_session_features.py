"""Behaviors the coding agent actually enforces."""
from pathlib import Path

from ronin_cli.code_tools import build_code_tools
from ronin_cli.session_features import SessionFeatures


def _tools(root: Path) -> dict:
    return {tool.name: tool.handler for tool in build_code_tools(root)}


def test_plan_mode_blocks_writes_until_turned_off(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    assert tools["set_plan_mode"](mode="plan") == "plan"
    refused = tools["write_file"](path="main.py", content="print(1)\n")
    assert refused.startswith("refused: plan mode")
    assert not (tmp_path / "main.py").exists()
    tools["set_plan_mode"](mode="default")
    assert tools["write_file"](path="main.py", content="print(1)\n").startswith("wrote")


def test_secret_files_and_permission_globs_are_refused(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    assert "secret" in tools["write_file"](path=".env", content="TOKEN=1\n")
    (tmp_path / ".ronin").mkdir()
    (tmp_path / ".ronin" / "permissions.txt").write_text("secrets/*\n", encoding="utf-8")
    assert "permission rule" in tools["write_file"](path="secrets/keys.txt", content="nope")


def test_shell_destructive_commands_are_refused(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    assert tools["run_command"](command="rm -rf /").startswith("refused:")
    assert tools["run_command"](command="echo ok").startswith("exit=")


def test_rewind_restores_the_previous_file(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    tools["write_file"](path="main.py", content="one\n")
    tools["edit_file"](path="main.py", old_string="one", new_string="two")
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "two\n"
    assert "rewound" in tools["rewind_edit"]()
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "one\n"


def test_todos_instructions_budget_and_compact(tmp_path: Path) -> None:
    features = SessionFeatures(tmp_path)
    (tmp_path / "RONIN.md").write_text("prefer small diffs\n", encoding="utf-8")
    assert "prefer small diffs" in features.instructions()
    assert features.add_todo("fix the gate") == 1
    assert features.complete_todo(1) == "fix the gate"
    assert "[x]" in features.todo_lines()
    features.set_budget(0)
    tools = _tools(tmp_path)
    assert "budget" in tools["write_file"](path="main.py", content="x")
    assert features.compact("a\nb\nc\nd\ne\n", keep=2).startswith("[compacted 3")
