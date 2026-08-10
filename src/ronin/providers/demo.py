"""Run the provider layer end to end, offline: ``uv run python -m ronin.providers.demo``.

Proves the headline claim by doing it. The same request, the same tools, the same
loop-facing client — three different providers, chosen by *config*, and the
normalized tool call is identical in all three. Then the two things you cannot see
from a passing test suite: what the format shim actually puts in a prompt, and what
the ledger says a session cost.

No network, no keys, no money. Every byte comes from the golden fixtures the test
suite uses, so the demo cannot drift from what is tested.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.types import DangerLevel, Message, Role, Text, ToolSpec
from .accounting import Ledger
from .assembly import CacheStats, StablePrefix, assemble
from .base import ModelClient, Transport
from .mlx_local import MLXClient
from .openai_compat import MoonshotClient, OpenAICompatClient
from .registry import build_client, describe
from .router import ModelSpec, Router, parse_config
from .router import Role as ModelRole
from .shim import ShimClient, build_shim_system
from .types import Completed, ModelDelta, ModelRequest, StreamReset, TextDelta, Usage

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "providers" / "fixtures"

TOOLS = (
    ToolSpec(
        name="read_file",
        description="Read a file from disk.",
        json_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="delete_file",
        description="Delete a file. Cannot be undone.",
        json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    ),
)

CONFIG = {
    "roles": {"main": "kimi-k2", "plan": "claude", "fast": "qwen-local"},
    "models": {
        "kimi-k2": {
            "provider": "moonshot",
            "model": "kimi-k2-0711-preview",
            "api_key_env": "MOONSHOT_API_KEY",
            "price_in": 0.60,
            "price_out": 2.50,
        },
        "claude": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key_env": "ANTHROPIC_API_KEY",
            "price_in": 3.00,
            "price_out": 15.00,
            "price_cache_read": 0.30,
        },
        "qwen-local": {
            "provider": "mlx",
            "model": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
            "native_tools": False,
            "max_context": 32768,
        },
    },
}


class FixtureTransport:
    """Replays a recorded response. The demo's stand-in for the network."""

    def __init__(self, fixture: str) -> None:
        self._path = FIXTURES / fixture

    async def send(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        del url, headers, body
        # Deliberately awkward chunking: the boundaries land mid-tag and mid-JSON,
        # which is what the reassembly logic exists for.
        data = self._path.read_bytes()
        for index in range(0, len(data), 7):
            yield data[index : index + 7]


def fixture_tokens(name: str) -> Any:
    lines = (FIXTURES / name).read_text().split("\n")
    tokens = tuple(line for line in lines if line != "")

    def generate(prompt: str, max_tokens: int) -> Iterator[str]:
        del prompt, max_tokens
        yield from tokens

    return generate


def rule(title: str) -> None:
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


async def collect(stream: AsyncIterator[ModelDelta]) -> tuple[str, Completed]:
    """Consume a stream the way a correct consumer must — resets included.

    ``StreamReset`` means "a retry is about to re-stream this turn; discard what you
    have shown". A consumer that appends straight through it prints the answer once
    per attempt. Writing this demo without the reset branch is what surfaced that,
    so the branch stays here as the reference behaviour.
    """
    text: list[str] = []
    completed: Completed | None = None
    async for delta in stream:
        if isinstance(delta, TextDelta):
            text.append(delta.text)
        elif isinstance(delta, StreamReset):
            text.clear()
        elif isinstance(delta, Completed):
            completed = delta
    assert completed is not None
    return "".join(text), completed


# --------------------------------------------------------------------------- #
# 1. Three providers, one normalized call
# --------------------------------------------------------------------------- #


async def demo_one_shape() -> None:
    rule("1. Three providers, three wire formats, one ToolUse")
    print(
        "The same logical response — say a sentence, then read src/main.py — as each\n"
        "provider actually puts it on the wire. Bytes replayed from the golden fixtures.\n"
    )

    from .anthropic import AnthropicClient

    clients: tuple[tuple[str, ModelClient], ...] = (
        (
            "anthropic",
            AnthropicClient(
                model="claude-sonnet-4-6", transport=FixtureTransport("anthropic/happy.sse")
            ),
        ),
        (
            "openai-compatible",
            OpenAICompatClient(
                model="gpt-x",
                transport=FixtureTransport("openai/happy.sse"),
                base_url="openai",
            ),
        ),
        (
            "moonshot / kimi k2",
            MoonshotClient(
                model="kimi-k2-0711-preview",
                transport=FixtureTransport("moonshot/happy.sse"),
            ),
        ),
        (
            "local mlx (shimmed)",
            ShimClient(MLXClient(model="qwen-local", generate=fixture_tokens("mlx/happy.tokens"))),
        ),
    )

    req = ModelRequest(model="demo", system="You are Ronin.", tools=TOOLS)
    normalized: set[str] = set()
    for label, client in clients:
        text, completed = await collect(client.stream(req))
        use = completed.tool_uses[0]
        normalized.add(f"{use.name}{sorted(use.arguments.items())}")
        caps = client.capabilities()
        tools_kind = "native" if caps.native_tools else "shimmed"
        print(f"  {label:<22} {tools_kind:<8} → {use.name}({dict(use.arguments)})")
        print(f"  {'':<22} visible text: {text!r}")
        if completed.notes:
            for note in completed.notes:
                print(f"  {'':<22} note: {note}")

    print()
    if len(normalized) == 1:
        print(f"  ✔ all four normalized to the same call: {normalized.pop()}")
    else:  # pragma: no cover - the test suite asserts this cannot happen
        print(f"  ✗ providers disagreed: {normalized}")


# --------------------------------------------------------------------------- #
# 2. What the shim puts in the prompt, and how it recovers
# --------------------------------------------------------------------------- #


async def demo_shim() -> None:
    rule("2. The format shim: what a weak model is actually asked for")
    system = build_shim_system("You are Ronin.", TOOLS)
    for line in system.splitlines()[:6]:
        print(f"  │ {line}")
    print(f"  │ … ({len(system.splitlines())} lines total)")

    print("\n  Two calls crammed into one block — both survive:")
    client = ShimClient(
        MLXClient(model="q", generate=fixture_tokens("mlx/two_calls_one_block.tokens"))
    )
    _, completed = await collect(client.stream(ModelRequest(model="q", tools=TOOLS)))
    for use in completed.tool_uses:
        print(f"    → {use.name}({dict(use.arguments)})")

    print("\n  Prose that only looks like a tag — nothing is swallowed, nothing leaks:")
    client = ShimClient(
        MLXClient(model="q", generate=fixture_tokens("mlx/angle_bracket_prose.tokens"))
    )
    text, completed = await collect(client.stream(ModelRequest(model="q", tools=TOOLS)))
    print(f"    visible: {text!r}")
    print(f"    calls:   {len(completed.tool_uses)}")

    print("\n  A call that never closes — surfaced, not hung, and not shown as prose:")
    client = ShimClient(
        MLXClient(model="q", generate=fixture_tokens("mlx/malformed_unterminated.tokens")),
        max_repairs=1,
    )
    text, completed = await collect(client.stream(ModelRequest(model="q", tools=TOOLS)))
    print(f"    finish:  {completed.finish.value}")
    print(f"    visible: {text!r}")
    for note in completed.notes:
        print(f"    note:    {note}")


# --------------------------------------------------------------------------- #
# 3. Roles, and where the money goes
# --------------------------------------------------------------------------- #


def demo_router() -> None:
    rule("3. Roles map to models in config — swapping one is not a code change")
    config = parse_config(CONFIG)

    def build(
        spec: ModelSpec,
        *,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ModelClient:
        del transport
        fixture = {
            "moonshot": "moonshot/happy.sse",
            "anthropic": "anthropic/happy.sse",
        }.get(spec.provider)
        if fixture is None:
            return build_client(spec, env=env)
        return build_client(spec, transport=FixtureTransport(fixture), env=env)

    router = Router(config, build=build, env={})
    for role in ModelRole:
        spec = router.spec_for(role)
        print(f"  {role.value:<6} {describe(spec, router.client_for(role))}")

    subagent = router.spec_for(ModelRole.FAST)
    print(
        f"\n  subagents and compaction → {subagent.name} "
        f"({'unpriced, local' if not subagent.priced else 'priced'})"
    )
    print("  neither helper takes a role argument, so neither can be pointed at 'main'")


def demo_accounting() -> None:
    rule("4. Cache-aware assembly, then what it cost")
    prefix = StablePrefix(
        system="You are Ronin.", tools=TOOLS, repo_map="src/\n  main.py\n  loop.py"
    )
    config = parse_config(CONFIG)
    claude = config.models["claude"]
    caps = build_client(claude, transport=FixtureTransport("anthropic/happy.sse")).capabilities()

    print(f"  stable prefix fingerprint: {prefix.fingerprint()}")
    req = assemble(prefix, [], model=claude.model, capabilities=caps)
    print(f"  cache marker at stable block {req.cache_marker} (system, repo map, tools)")

    stats = CacheStats()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(Path(tmp) / "usage.db", clock=lambda: 1_700_000_000.0)

        # Turn 1 writes the cache; turns 2-5 read it. Same prefix every time.
        turns = (
            Usage(input_tokens=1200, output_tokens=180, cache_write_tokens=8000),
            Usage(input_tokens=90, output_tokens=210, cache_read_tokens=8000),
            Usage(input_tokens=140, output_tokens=160, cache_read_tokens=8000),
            Usage(input_tokens=110, output_tokens=190, cache_read_tokens=8000),
        )
        for index, usage in enumerate(turns):
            message = Message(role=Role.USER, content_blocks=(Text(f"turn {index}"),))
            req = assemble(prefix, [message], model=claude.model, capabilities=caps)
            fingerprint = str(req.metadata["prefix_fingerprint"])
            stats.record(usage, fingerprint=fingerprint)
            ledger.record(
                session_id="demo",
                request_id=f"main-{index}",
                role=ModelRole.MAIN,
                spec=claude,
                usage=usage,
                prefix_fingerprint=fingerprint,
            )

        # Twenty cheap search/summarize calls, routed to the local model.
        local = config.models["qwen-local"]
        for index in range(20):
            ledger.record(
                session_id="demo",
                request_id=f"fast-{index}",
                role=ModelRole.FAST,
                spec=local,
                usage=Usage(),
                reported=False,
            )

        print(f"  {stats.summary()}")
        print(f"  distinct prefixes sent: {len(stats.fingerprints)} (1 means it never moved)")
        print(f"  written but never read:  {stats.write_only_prefixes or '(none)'}")

        print("\n  ledger, per role:")
        for role, totals in ledger.role_totals("demo").items():
            print(f"    {role.value:<6} {totals.summary()}")
        print(f"\n  session total: {ledger.session_totals('demo').summary()}")
        print(
            "  the 20 fast calls are unpriced *and* unreported — the summary says so\n"
            "  rather than implying they were free"
        )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


async def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print("ronin.providers — offline demo. No network, no keys, no spend.")
    if not FIXTURES.exists():  # pragma: no cover - only in a source-less install
        print(f"fixtures not found at {FIXTURES}; run this from a source checkout")
        return 1
    await demo_one_shape()
    await demo_shim()
    demo_router()
    demo_accounting()
    rule("done")
    print("Every byte above came from tests/providers/fixtures — the same bytes the")
    print("suite asserts on, so this demo cannot drift from what is tested.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
