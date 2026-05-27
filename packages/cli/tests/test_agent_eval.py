from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ro_claude_kit_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ro_claude_kit_cli.agent_eval import EvalTask, run_eval
from ro_claude_kit_cli.config import CSKConfig


def test_run_eval_scores_pass_and_fail(tmp_path: Path) -> None:
    config = CSKConfig(provider="groq", openai_api_key="x")

    pass_task = EvalTask("echo", "say hello", lambda r, root: "hello" in r.output.lower())
    fail_task = EvalTask("nope", "say hello", lambda r, root: "goodbye" in r.output.lower())

    provider = FakeProvider(responses=[LLMResponse(text="hello there!", stop_reason="end_turn", usage={})])
    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=provider):
        outcomes = run_eval(config, tasks=[pass_task, fail_task])

    assert [o.passed for o in outcomes] == [True, False]
    assert outcomes[0].task.id == "echo"


def test_run_eval_checks_file_side_effects(tmp_path: Path) -> None:
    config = CSKConfig(provider="groq", openai_api_key="x")
    task = EvalTask(
        "write", "create note.txt",
        lambda r, root: (root / "note.txt").is_file(),
    )
    provider = FakeProvider(responses=[
        LLMResponse(text="", tool_calls=[ToolCall(id="t1", name="write_file",
                    arguments={"path": "note.txt", "content": "hi"})],
                    stop_reason="tool_use", usage={}),
        LLMResponse(text="done", stop_reason="end_turn", usage={}),
    ])
    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=provider):
        outcomes = run_eval(config, tasks=[task])
    assert outcomes[0].passed
