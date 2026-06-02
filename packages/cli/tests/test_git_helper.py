from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from ronin_agent_patterns import FakeProvider, LLMResponse
from ronin_cli import git_helper
from ronin_cli.config import RoninConfig


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def test_commit_drafts_message_and_commits(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f.txt").write_text("hello")

    console = Console(file=io.StringIO(), force_terminal=False)
    provider = FakeProvider(responses=[
        LLMResponse(text="feat: add f.txt", stop_reason="end_turn", usage={})])
    with patch("ronin_cli.runner.build_provider", return_value=provider), \
         patch("builtins.input", return_value="y"):
        git_helper.commit(console, tmp_path, RoninConfig(provider="groq", openai_api_key="x"))

    log = subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "feat: add f.txt" in log


def test_commit_clean_tree_noops(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    console = Console(file=(buf := io.StringIO()), force_terminal=False)
    git_helper.commit(console, tmp_path, RoninConfig(provider="groq", openai_api_key="x"))
    assert "clean" in buf.getvalue()
