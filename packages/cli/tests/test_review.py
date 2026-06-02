from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from ronin_agent_patterns import FakeProvider, LLMResponse
from ronin_cli.config import RoninConfig
from ronin_cli.review_mode import run_review


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def test_review_streams_findings_on_a_diff(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    (tmp_path / "app.py").write_text("def f():\n    return 2  # changed\n")

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    provider = FakeProvider(responses=[
        LLMResponse(text="🟢 Nit: looks fine. Ship it.", stop_reason="end_turn", usage={})])
    with patch("ronin_cli.code_mode.build_provider", return_value=provider):
        ok = run_review(RoninConfig(provider="groq", openai_api_key="x"), root=tmp_path, console=console)
    assert ok is True
    assert "reviewing working-tree changes" in buf.getvalue()


def test_review_clean_tree_returns_false(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    ok = run_review(RoninConfig(provider="groq", openai_api_key="x"), root=tmp_path, console=console)
    assert ok is False
    assert "no working-tree changes" in buf.getvalue()
