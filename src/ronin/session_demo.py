"""All three layers running together: ``uv run python -m ronin.session_demo``.

The loop, a provider, and the tools — with ``task`` wired to a real nested turn on
the ``fast`` model. Offline: the models are scripted fakes, the files are in a temp
directory, and nothing opens a socket or spends money.

What it demonstrates, in order:

1. A parent turn on the **main** model that calls ``task``.
2. A nested turn on the **fast** model that reads files and reports a conclusion.
3. The parent receiving *only* that conclusion — the child's tool results never
   entered the parent's transcript.
4. The ledger showing the split: the expensive model did one turn, the cheap one did
   the sweeping.
"""
from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path

from .core.loop import run_turn
from .core.types import (
    ApprovalDecision,
    Budget,
    Message,
    Role,
    Text,
    ToolSpec,
    ToolUse,
    TurnEnd,
)
from .core.types import (
    TextDelta as CoreTextDelta,
)
from .providers import (
    Capabilities,
    Completed,
    FinishReason,
    Ledger,
    ModelRequest,
    ModelSpec,
    RouterConfig,
    TextDelta,
    Usage,
)
from .providers.base import ModelClient
from .providers.router import Role as ModelRole
from .providers.router import Router
from .providers.types import ModelDelta
from .session import build_session
from .tools import build_registry
from .tools.base import ToolContext

CAPS = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=False,
    thinking=False,
    max_context=100_000,
    vision=False,
)


class Scripted:
    """A model that replays a scripted turn per call."""

    def __init__(self, name: str, script: Sequence[Sequence[ModelDelta]]) -> None:
        self.name = name
        self.script = tuple(script)
        self.calls = 0

    def capabilities(self) -> Capabilities:
        return CAPS

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        index = min(self.calls, len(self.script) - 1)
        self.calls += 1
        for delta in self.script[index]:
            yield delta


def says(text: str, *, tokens: int) -> tuple[ModelDelta, ...]:
    return (
        TextDelta(text),
        Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
            finish=FinishReason.STOP,
            usage=Usage(input_tokens=tokens, output_tokens=tokens // 4, cost_usd=0.0),
        ),
    )


def calls(name: str, *, tokens: int, **args: object) -> tuple[ModelDelta, ...]:
    return (
        Completed(
            message=Message(
                role=Role.ASSISTANT,
                content_blocks=(ToolUse(id=f"c_{name}", name=name, arguments=args),),
            ),
            finish=FinishReason.TOOL_USE,
            usage=Usage(input_tokens=tokens, output_tokens=tokens // 4, cost_usd=0.0),
        ),
    )


class AskNothing:
    """The parent's policy for the demo: allow everything, no budget, no interrupt."""

    async def approve(
        self, spec: ToolSpec, use: ToolUse, *, rendered: str
    ) -> ApprovalDecision:
        del spec, use, rendered
        return ApprovalDecision(approved=True, reason="demo policy")

    def check_budget(self, budget: Budget) -> str | None:
        del budget
        return None

    def cancelled(self) -> bool:
        return False


def rule(title: str) -> None:
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


def seed(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src/app.py").write_text("def handler():\n    return None\n")
    (root / "src/util.py").write_text("def helper():\n    return None\n")
    (root / "src/db.py").write_text("SECRET = 'this must not reach the parent'\n")


def build(models: Mapping[str, ModelClient]) -> Router:
    config = RouterConfig(
        roles={ModelRole.MAIN: "big", ModelRole.FAST: "small"},
        models={
            "big": ModelSpec(
                name="big",
                provider="anthropic",
                model="claude-sonnet-4-6",
                price_in=3.0,
                price_out=15.0,
            ),
            "small": ModelSpec(
                name="small", provider="mlx", model="mlx-community/Qwen2.5-Coder-7B"
            ),
        },
    )

    def factory(spec: ModelSpec, *, transport: object = None, env: object = None) -> ModelClient:
        del transport, env
        return models[spec.name]

    return Router(config, build=factory)


async def main() -> int:
    print("ronin — the loop, a provider and the tools, wired together. Offline.")
    with tempfile.TemporaryDirectory(prefix="ronin-session-demo-") as tmp:
        root = Path(tmp)
        seed(root)

        # The child sweeps three files, then reports one sentence.
        child = Scripted(
            "fast",
            [
                calls("grep", tokens=800, pattern="return None", mode="files"),
                calls("read", tokens=1400, path="src/app.py"),
                calls("read", tokens=2100, path="src/db.py"),
                says(
                    "Two functions return None: src/app.py:2 (handler) and "
                    "src/util.py:2 (helper). src/db.py holds a credential constant.",
                    tokens=2800,
                ),
            ],
        )
        # The parent delegates, then answers from the child's one sentence.
        parent = Scripted(
            "main",
            [
                calls(
                    "task",
                    tokens=1200,
                    prompt="Find every function that returns None and say where.",
                    subagent_type="explore",
                ),
                says(
                    "Two: handler in src/app.py and helper in src/util.py. "
                    "I'd start with handler.",
                    tokens=1500,
                ),
            ],
        )

        router = build({"big": parent, "small": child})
        ctx = ToolContext(root=root)
        ledger = Ledger(root / "usage.db", clock=lambda: 1_700_000_000.0)
        session = build_session(
            router,
            ctx,
            base_tools=build_registry(ctx),
            ledger=ledger,
            session_id="demo",
        )

        rule("1. one parent turn, which delegates")
        state = session.state("Where do we return None?")
        text: list[str] = []
        stop = ""
        final = state
        async for event in run_turn(state, session.main, session.registry, AskNothing()):
            if isinstance(event, CoreTextDelta):
                text.append(event.text)
            elif isinstance(event, TurnEnd):
                stop = event.stop_reason
                if event.agent_state is not None:
                    final = event.agent_state
        session.record_turn(final, request_id="turn-1")
        answer = "".join(text)
        print("  user:   Where do we return None?")
        print(f"  agent:  {answer}")
        print(f"  stopped on: {stop}")

        rule("2. what the child did, and what escaped it")
        run = session.subagents.runs[0]
        print(f"  the child made {child.calls} model call(s) and ran 3 tools")
        print(f"  {run.line()}")
        print(f"\n  what came back to the parent:\n    {run.summary}")

        leaked = "this must not reach the parent" in answer
        print("\n  the child read src/db.py, which holds a credential.")
        print(f"  did it reach the parent's answer? {'YES — BUG' if leaked else 'no'}")

        rule("3. the split, from the ledger")
        for role, totals in ledger.role_totals("demo").items():
            print(f"  {role.value:<6} {totals.summary()}")
        print(f"\n  {session.subagents.summary()}")
        print(
            "\n  The expensive model saw one delegation and one sentence. The cheap\n"
            "  one did the sweeping. That is the routing rule, actually enforced."
        )

        rule("4. the parent's transcript stayed clean")
        print(f"  the parent made {parent.calls} model call(s)")
        print(f"  files the *parent* has read: {sorted(Path(p).name for p in ctx.read_files)}")
        print(
            "  ↑ empty: the child's reads are its own, so the parent's write guard is\n"
            "    not weakened by work it never saw."
        )

    rule("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
