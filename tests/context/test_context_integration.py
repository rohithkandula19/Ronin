"""The five pieces wired together, with fakes only at the summarizer seam.

One simulated session: build a real repo map from a real tree, load real RONIN.md
files, read and edit real files through the tracker, clamp every tool result through
the budgeter, and compact when the window fills. The assertions are the properties
the orchestrator depends on, not the internals of any one module.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from context_harness import fake_summarizer, plant

from ronin.context import (
    CompactionPolicy,
    FileStateTracker,
    FileStatus,
    OutputBudget,
    build_repo_map,
    clamp,
    estimate_tokens,
    load_memory,
    maybe_compact,
    propose_bootstrap,
    remember,
)
from ronin.context.demo import main as demo_main
from ronin.core.types import (
    Message,
    Role,
    Text,
    ToolResultBlock,
    ToolUse,
    unpaired_tool_uses,
)

TREE = {
    ".gitignore": "node_modules/\n*.log\n",
    "RONIN.md": "## Test\n- run `pytest -q`\n",
    "Makefile": "test:\n\tpytest -q\n",
    "src/engine.py": (
        "class Engine:\n"
        "    def run(self, steps: int) -> str:\n"
        "        return 'ok'\n"
    ),
    "src/app.py": "from .engine import Engine\n\n\ndef main() -> int:\n    return 0\n",
    "src/util.py": "from .engine import Engine\n\n\ndef helper(x: int) -> int:\n    return x\n",
    "node_modules/dep/index.js": "export function hidden() {}\n",
    "build.log": "noise\n",
}


async def test_a_simulated_session_holds_every_invariant(tmp_path: Path) -> None:
    root = plant(tmp_path / "repo", TREE)
    cache = tmp_path / "cache"

    # ---- the stable prefix: repo map + memory, both inside their budgets
    repo_map = build_repo_map(root, budget_tokens=300, cache_dir=cache)
    memory = load_memory(root / "src", root=root)
    assert repo_map.token_estimate <= 300
    assert set(repo_map.paths) == {"src/app.py", "src/engine.py", "src/util.py"}
    assert "hidden" not in repo_map.render()
    assert "run `pytest -q`" in memory.render()
    prefix_tokens = repo_map.token_estimate + estimate_tokens(memory.render())

    # ---- the session: read a file, clamp the result, track it, edit it
    tracker = FileStateTracker()
    budget = OutputBudget(remaining_chars=400)
    transcript: list[Message] = [
        Message(role=Role.SYSTEM, content_blocks=(Text("system prompt"),))
    ]
    watched = root / "src" / "engine.py"
    for turn in range(1, 40):
        call_id = f"call-{turn}"
        target = watched if turn % 5 else root / "src" / "util.py"
        tracker.record_read(target)
        raw = target.read_bytes().decode() + "".join(
            f"# padding line {index}\n" for index in range(60)
        )
        clamped = clamp(raw, budget=budget)
        assert len(clamped.text) <= 400
        transcript.append(
            Message(role=Role.USER, content_blocks=(Text(f"turn {turn}: look at {target.name}"),))
        )
        transcript.append(
            Message(
                role=Role.ASSISTANT,
                content_blocks=(
                    ToolUse(
                        id=call_id,
                        name="read",
                        arguments={"path": str(target.relative_to(root))},
                    ),
                ),
            )
        )
        transcript.append(
            Message(
                role=Role.TOOL,
                content_blocks=(ToolResultBlock(tool_use_id=call_id, content=clamped.text),),
            )
        )

    # ---- the user edits a file behind the agent's back
    watched.write_bytes(watched.read_bytes() + b"# the user's own fix\n")
    verdict = tracker.check(watched)
    assert verdict.status is FileStatus.CHANGED
    assert not verdict.safe_to_edit
    assert "read" in verdict.message

    # ---- the window fills and compaction fires, counting the stable prefix
    policy = CompactionPolicy(context_window=3000)
    result = await maybe_compact(
        transcript, policy=policy, summarizer=fake_summarizer, pinned_prefix_tokens=prefix_tokens
    )
    assert result.compacted
    assert unpaired_tool_uses(result.messages) == ()
    assert result.token_estimate_after < result.token_estimate_before
    assert result.marker in result.messages[1].text
    # the two files the session actually touched are both still readable in full
    assert set(result.retained_paths) == {"src/engine.py", "src/util.py"}

    # ---- and a rule learned mid-session is durable
    remember("engine.run returns a str, not an int", cwd=root / "src", root=root)
    assert "returns a str" in load_memory(root / "src", root=root).render()

    # ---- the bootstrap draft only claims what the tree contains
    draft = propose_bootstrap(root)
    assert "`make test`" in draft
    assert "none found: no lint command" in draft


async def test_the_cache_survives_a_second_session_over_the_same_tree(tmp_path: Path) -> None:
    root = plant(tmp_path / "repo", TREE)
    cache = tmp_path / "cache"
    first = build_repo_map(root, cache_dir=cache)
    second = build_repo_map(root, cache_dir=cache)
    assert second.reused_files == first.parsed_files
    assert second.render() == first.render()


async def test_the_demo_runs_offline_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert await demo_main() == 0
    output = capsys.readouterr().out
    assert "REPO MAP" in output
    assert "lines elided" in output
    assert "unpaired tool uses: ()" in output
    assert "written in turn 3" in output
