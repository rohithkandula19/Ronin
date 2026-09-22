"""The local coding kit exposes 100 commands and runs them without a model."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ronin_cli.coding_kit import command_names, kit_app


def test_one_hundred_commands() -> None:
    assert len(command_names()) == 100
    assert len(kit_app.registered_commands) == 100
    assert len(set(command_names())) == 100


def test_status_and_todos_on_a_real_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.dev"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "main.py").write_text("# TODO: ship\nprint('ok')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "main.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)

    from ronin_cli import coding_kit as kit

    assert "TODO 1" in kit._todos(tmp_path)
    assert "main.py" in kit._py_files(tmp_path)
    assert kit._dirty(tmp_path) == "no"
    assert len(kit.command_names()) == 100
