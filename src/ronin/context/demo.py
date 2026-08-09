"""See the context layer work: ``python -m ronin.context.demo``.

Offline and self-contained: a synthetic repo in a temp directory, a summarizer that
is a plain function, and no network anywhere. Each section prints the thing that
would otherwise only be visible in a passing test — the refusals, the markers, and
the counts.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ..core.types import Message, Role, Text, ToolResultBlock, ToolUse, unpaired_tool_uses
from .budget import OutputBudget, clamp
from .compaction import (
    CompactionPolicy,
    compact,
    should_compact,
    transcript_tokens,
)
from .filestate import FileStateTracker
from .memory import load_memory, propose_bootstrap, remember
from .repomap import build_repo_map

TREE: dict[str, str] = {
    ".gitignore": "node_modules/\n*.log\n!keep.log\nbuild/\n",
    "package.json": '{"scripts": {"build": "tsc", "test": "vitest run"}}\n',
    "Makefile": "lint:\n\truff check .\n\ncoverage:\n\tpytest --cov\n",
    "src/engine.py": (
        '"""The thing everything imports."""\n'
        "MAX_STEPS = 12\n\n\n"
        "class Engine:\n"
        "    def run(self, steps: int) -> str:\n"
        "        return 'done'\n\n"
        "    def _private(self) -> None:\n"
        "        pass\n\n\n"
        "def build(name: str, *, strict: bool = False) -> Engine:\n"
        "    return Engine()\n"
    ),
    "src/app.py": (
        "from .engine import Engine, build\n\n\n"
        "def main(argv: list[str]) -> int:\n"
        "    return 0\n"
    ),
    "src/cli.py": (
        "from .app import main\n\n\n"
        "def entry() -> int:\n"
        "    return main([])\n"
    ),
    "src/orphan.py": "def nobody_imports_me() -> None:\n    pass\n",
    "src/broken.py": "def oops(:\n",
    "node_modules/dep/index.js": "export function hidden() {}\n",
    "debug.log": "should be ignored\n",
    "keep.log": "re-included by a negation\n",
    "RONIN.md": "## Build\n- `make lint` before every commit.\n",
    "src/RONIN.md": "## Build\n- inside src/, run `pytest tests/unit` (overrides the root rule).\n",
}


def rule(title: str) -> None:
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


def plant(root: Path) -> None:
    (root / ".git").mkdir()
    for relative, content in TREE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


async def fake_summarizer(prompt: str) -> str:
    """Stands in for the fast model. The orchestrator wires the real one."""
    return (
        "## Goal\nWire the engine into the CLI.\n\n"
        "## Files touched\n- src/turn3.py — rewritten by edit\n\n"
        "## Learned\n- Engine.run returns a string, not an int.\n\n"
        "## Failed\n- `pytest -x` failed twice on a stale import.\n\n"
        f"## Remaining\n- finish the CLI wiring (summarized {len(prompt)} chars)\n"
    )


def scripted_session(turns: int) -> list[Message]:
    """A transcript shaped like a real one: user turn, tool call, tool result.

    Turn 3 edits ``src/turn3.py`` and nothing touches it again, which is the fact
    the retention rule has to preserve through every later compaction.
    """
    messages: list[Message] = [
        Message(
            role=Role.SYSTEM,
            content_blocks=(Text("You are ronin. [repo map + RONIN.md pinned here]"),),
        )
    ]
    for turn in range(1, turns + 1):
        path = "src/turn3.py" if turn == 3 else f"src/file{turn}.py"
        content = (
            "def answer() -> int:\n    return 42  # written in turn 3\n"
            if turn == 3
            else f"# routine turn {turn}\n"
        )
        call_id = f"call-{turn}"
        messages.append(
            Message(role=Role.USER, content_blocks=(Text(f"turn {turn}: edit {path}"),))
        )
        messages.append(
            Message(
                role=Role.ASSISTANT,
                content_blocks=(
                    Text(f"editing {path}"),
                    ToolUse(id=call_id, name="write", arguments={"path": path, "content": content}),
                ),
            )
        )
        messages.append(
            Message(
                role=Role.TOOL,
                content_blocks=(
                    ToolResultBlock(tool_use_id=call_id, content=f"wrote {path}\n{content}"),
                ),
            )
        )
    return messages


async def main() -> int:
    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace) / "repo"
        root.mkdir()
        plant(root)
        cache = Path(workspace) / "cache"
        home = Path(workspace) / "home"
        (home / ".ronin").mkdir(parents=True)
        (home / ".ronin" / "RONIN.md").write_text(
            "## Style rules\n- never commit to main.\n", encoding="utf-8"
        )

        # ------------------------------------------------------------ repo map
        rule("1. REPO MAP — signatures only, gitignore respected, pagerank ranked")
        repo_map = build_repo_map(root, budget_tokens=2000, cache_dir=cache)
        print(repo_map.render())
        print(f"  token estimate: {repo_map.token_estimate} (budget {repo_map.budget_tokens})")
        print(f"  parsed {repo_map.parsed_files} file(s), reused {repo_map.reused_files}")
        print("  node_modules/ and debug.log are absent; keep.log was re-included by '!'")
        print("  src/broken.py is listed with its syntax error, not silently skipped")

        again = build_repo_map(root, budget_tokens=2000, cache_dir=cache)
        print(f"  second build: parsed {again.parsed_files}, reused {again.reused_files}")
        (root / "src" / "app.py").write_text(
            "from .engine import Engine\n\n\ndef main(argv: list[str], *, verbose: bool) -> int:\n"
            "    return 1\n",
            encoding="utf-8",
        )
        touched = build_repo_map(root, budget_tokens=2000, cache_dir=cache)
        print(
            f"  after editing one file: parsed {touched.parsed_files}, "
            f"reused {touched.reused_files} — one changed mtime re-parses one file"
        )

        tight = build_repo_map(root, budget_tokens=110, cache_dir=cache)
        print(f"\n  with a 110-token budget, leaves go first:\n  {tight.marker()}")
        print(f"  kept: {list(tight.paths)}")
        print(f"  over budget: {tight.over_budget} (only the marker overflows)")

        # -------------------------------------------------------------- memory
        rule("2. MEMORY — RONIN.md, global then outermost to innermost")
        memory = load_memory(root / "src", home=home, root=root)
        print(memory.render())
        rule_text = "run `uv run pytest tests/context -q` before pushing"
        target = remember(rule_text, cwd=root / "src", root=root)
        again_path = remember(rule_text, cwd=root / "src", root=root)
        print(
            f"  remembered into {target.relative_to(root)} "
            f"(idempotent: {target == again_path})"
        )
        print("  " + "\n  ".join(target.read_text(encoding="utf-8").splitlines()))

        rule("2b. AUTO-BOOTSTRAP — only commands with evidence in the tree")
        print(propose_bootstrap(root))

        # ---------------------------------------------------------- compaction
        rule("3. COMPACTION — 200 turns folded, turn 3's file kept in full")
        transcript = scripted_session(200)
        policy = CompactionPolicy(context_window=8000)
        print(f"  before: {len(transcript)} messages, ~{transcript_tokens(transcript)} tokens")
        fires = should_compact(transcript, policy=policy)
        print(f"  trigger at {policy.trigger_tokens} tokens: {fires}")
        result = await compact(transcript, policy=policy, summarizer=fake_summarizer)
        print(f"  after:  {len(result.messages)} messages, ~{result.token_estimate_after} tokens")
        print(f"  marker: {result.marker}")
        surviving = "\n".join(
            block.content
            for message in result.messages
            for block in message.content_blocks
            if isinstance(block, ToolResultBlock)
        )
        print(f"  'written in turn 3' still in context: {'written in turn 3' in surviving}")
        print(f"  unpaired tool uses: {unpaired_tool_uses(result.messages)}")
        print(f"  retained paths: {len(result.retained_paths)} (one per unique file)")
        print(
            f"  still at/over the trigger: {result.still_over_trigger} — this scripted "
            "session touches a NEW file every turn, the worst case for per-path\n"
            "  retention. Unbounded retention is what keeps turn 3 answerable; bounding "
            "it trades that away:"
        )
        bounded = await compact(
            transcript,
            policy=CompactionPolicy(context_window=8000, max_retained_paths=20),
            summarizer=fake_summarizer,
        )
        bounded_text = "\n".join(
            block.content
            for message in bounded.messages
            for block in message.content_blocks
            if isinstance(block, ToolResultBlock)
        )
        print(
            f"    max_retained_paths=20 → ~{bounded.token_estimate_after} tokens, "
            f"over trigger: {bounded.still_over_trigger}, "
            f"turn 3 kept: {'written in turn 3' in bounded_text}"
        )

        # ----------------------------------------------------------- filestate
        rule("4. FILE STATE — catching an edit the user made while the model thought")
        tracker = FileStateTracker()
        watched = root / "src" / "engine.py"
        tracker.record_read(watched)
        print(f"  after reading:  {tracker.check(watched).status.value}")
        watched.write_text(
            watched.read_text(encoding="utf-8") + "# the user fixed a typo\n", encoding="utf-8"
        )
        verdict = tracker.check(watched)
        print(f"  after their edit: {verdict.status.value}")
        print(f"  message to the model: {verdict.message}")
        missing = root / "src" / "orphan.py"
        tracker.record_read(missing)
        missing.unlink()
        print(f"  deleted file: {tracker.check(missing).message}")
        print(f"  never read:   {tracker.check(root / 'src' / 'cli.py').message}")

        # -------------------------------------------------------------- budget
        rule("5. OUTPUT BUDGET — head 60% / tail 40%, marker names the true count")
        log = "".join(f"line {index:03d}: building target {index}\n" for index in range(200))
        clamped = clamp(log, budget=OutputBudget(remaining_chars=400))
        print(clamped.text)
        print(
            f"  {len(log)} chars in, {len(clamped.text)} out, "
            f"{clamped.elided_lines} lines elided"
        )
        short = "short output\n"
        untouched = clamp(short, budget=OutputBudget(remaining_chars=400))
        print(f"  under budget is byte-identical: {untouched.text == short}")
        blob = "x" * 5000
        single = clamp(blob, budget=OutputBudget(remaining_chars=200))
        print(f"  one enormous line falls back to chars: {single.marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
