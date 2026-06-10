from __future__ import annotations

import io
import shlex
import sys
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from ronin_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ronin_cli.config import RoninConfig
from ronin_cli.fix_mode import run_fix


def test_fix_loops_until_command_passes(tmp_path: Path) -> None:
    # buggy module: add() subtracts → the command's assert fails
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    command = (f"{shlex.quote(sys.executable)} -c "
               "\"ns={}; exec(open('calc.py').read(), ns); assert ns['add'](2,3)==5\"")

    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    # the agent fixes calc.py via write_file, then ends its turn
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="t1", name="write_file",
                    arguments={"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"})],
                    stop_reason="tool_use", usage={}),
        LLMResponse(text="fixed the operator", stop_reason="end_turn", usage={}),
    ])
    with patch("ronin_cli.code_mode.build_provider", return_value=provider):
        ok = run_fix(RoninConfig(provider="groq", openai_api_key="x"),
                     command, root=tmp_path, console=console, yolo=True)
    assert ok is True
    assert "def add(a, b):\n    return a + b" in (tmp_path / "calc.py").read_text()


def test_fix_returns_true_immediately_if_already_passing(tmp_path: Path) -> None:
    console = Console(file=io.StringIO(), force_terminal=False)
    ok = run_fix(RoninConfig(provider="groq", openai_api_key="x"),
                 f'{shlex.quote(sys.executable)} -c "assert True"', root=tmp_path, console=console)
    assert ok is True
