"""Fakes for the wire/doctor/wizard tests. Nothing here opens a socket or a process.

Named ``wire_harness`` rather than ``harness`` because the test trees are deliberately
not packages, so every module under ``tests/*`` is importable by its bare basename and
two directories cannot share one — ``tests/tools/harness.py`` already exists.

Four seams are faked, and only four: the model (a scripted ``ModelClient`` behind a
real ``Router``), the MCP transport (an in-process responder, plus one that dies on
``start``), ``shutil.which``, and the checkpoint store's subprocess runner. Everything
else is the real object against a real ``tmp_path`` — a mocked filesystem here would
test the mock, since reading a workspace off disk is exactly what ``load_workspace``
does.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ronin.cli.spine import Paths
from ronin.core.types import Message, Role, Text
from ronin.mcp.config import McpServerConfig
from ronin.mcp.jsonrpc import JsonRpcError, Request, Response, TransportClosed
from ronin.providers.base import ModelClient
from ronin.providers.router import ModelSpec, Router, RouterConfig
from ronin.providers.router import Role as ModelRole
from ronin.providers.types import (
    Capabilities,
    Completed,
    FinishReason,
    ModelDelta,
    Usage,
)
from ronin.verify.checkpoints import CheckpointStore
from ronin.verify.runner import CommandFailure, CommandOutcome

CAPS = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=True,
    thinking=False,
    max_context=100_000,
    vision=False,
)


def says(text: str) -> tuple[ModelDelta, ...]:
    """One turn that answers in prose and calls nothing."""
    return (
        Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
            finish=FinishReason.STOP,
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
        ),
    )


class ScriptedModel:
    """A ``ModelClient`` that replays one scripted turn per call and records requests."""

    def __init__(self, script: Sequence[Sequence[ModelDelta]] | None = None) -> None:
        self.script = tuple(script) if script else (says("done"),)
        self.requests: list[Any] = []

    def capabilities(self) -> Capabilities:
        return CAPS

    async def stream(self, req: Any) -> AsyncIterator[ModelDelta]:
        index = len(self.requests)
        self.requests.append(req)
        for delta in self.script[min(index, len(self.script) - 1)]:
            yield delta


ROUTER_CONFIG = RouterConfig(
    roles={ModelRole.MAIN: "big", ModelRole.FAST: "small"},
    models={
        "big": ModelSpec(name="big", provider="anthropic", model="claude-x"),
        "small": ModelSpec(name="small", provider="mlx", model="qwen-local"),
    },
)


def fake_router(config: RouterConfig = ROUTER_CONFIG, model: ModelClient | None = None) -> Router:
    """A real ``Router`` whose factory hands back one scripted client for every spec."""
    client = model if model is not None else ScriptedModel()

    def build(spec: ModelSpec, *, transport: object = None, env: object = None) -> ModelClient:
        del spec, transport, env
        return client

    async def no_sleep(delay: float) -> None:
        del delay

    return Router(config, build=build, sleep=no_sleep)


def keyed_router(env_var: str = "SOME_KEY") -> Router:
    """A router whose main model needs an API key, for the doctor's key check."""
    config = RouterConfig(
        roles={ModelRole.MAIN: "big"},
        models={
            "big": ModelSpec(
                name="big", provider="anthropic", model="claude-x", api_key_env=env_var
            )
        },
    )
    return fake_router(config)


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #


def workspace(root: Path, *, home: Path | None = None, cwd: Path | None = None) -> Paths:
    """A ``Paths`` rooted in ``root``, with a home that is never the real one."""
    home_dir = home if home is not None else root / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    return Paths(workspace_root=root, home=home_dir, cwd=cwd if cwd is not None else root)


def write(root: Path, relative: str, content: str) -> Path:
    """Create a file under ``root``, making parents. Explicit ``\\n`` newlines."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def git_repo(root: Path) -> Path:
    """Make ``root`` look like a git repo without running git.

    ``find_repo_root`` and ``CheckpointStore._probe`` both test ``(root / ".git")`` for
    existence only, and a worktree writes a *file* there — so a file is enough and
    costs no subprocess.
    """
    (root / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    return root


AGENT_DEFINITION = """\
---
name: sweeper
description: Sweeps the repo for a symbol and reports where it is defined.
tools: [read, grep, glob]
model: fast
---
You are the sweeper. Report file:line references and nothing else.
"""


def full_workspace(root: Path) -> Paths:
    """A workspace with all seven config sources present, plus git, memory and source.

    One of each: user settings, project settings, local settings, ``mcp.json``,
    ``hooks.json``, an agent definition and a slash command — so a single ``Loaded`` can
    be asserted against every loader at once, with provenance.
    """
    paths = workspace(root)
    git_repo(root)
    write(root, "RONIN.md", "# project\n\nRun the tests with `pytest -q`.\n")
    write(root, "pkg/thing.py", "def widen(value: int) -> int:\n    return value + 1\n")
    write(root, "pkg/other.py", "from pkg.thing import widen\n\nWIDE = widen(1)\n")
    write(root, "pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q"\n')
    write(
        paths.home,
        ".ronin/settings.json",
        json.dumps({"protected_branches": ["release"]}) + "\n",
    )
    write(root, ".ronin/settings.json", json.dumps({"mode": "auto_edit"}) + "\n")
    write(root, ".ronin/settings.local.json", json.dumps({"taint_min_span": 32}) + "\n")
    write(
        root,
        ".ronin/hooks.json",
        json.dumps({"PreToolUse": [{"matcher": "bash", "hooks": [{"command": "true"}]}]}) + "\n",
    )
    write(root, ".ronin/agents/sweeper.md", AGENT_DEFINITION)
    write(root, ".ronin/commands/ship.md", "Ship $ARGUMENTS to staging.\n")
    mcp_config(root, "docs")
    return paths


# --------------------------------------------------------------------------- #
# MCP
# --------------------------------------------------------------------------- #

TOOL_DESCRIPTOR: Mapping[str, Any] = {
    "name": "lookup",
    "description": "Look a term up in the docs index.",
    "inputSchema": {
        "type": "object",
        "properties": {"term": {"type": "string"}},
        "required": ["term"],
    },
}


@dataclass(slots=True)
class FakeMcpTransport:
    """An in-process MCP server: handshake, one tool, and a scripted call result."""

    protocol_version: str = "2025-06-18"
    tools: tuple[Mapping[str, Any], ...] = (TOOL_DESCRIPTOR,)
    supports_tools: bool = True
    started: bool = False
    closed: bool = False
    calls: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)

    async def start(self) -> None:
        self.started = True

    async def send(self, request: Request) -> Response:
        if not self.started:
            raise TransportClosed("send before start")
        if request.method == "initialize":
            return Response(
                id=request.id,
                result={
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {}} if self.supports_tools else {},
                    "serverInfo": {"name": "fake", "version": "1"},
                },
            )
        if request.method == "tools/list":
            return Response(id=request.id, result={"tools": list(self.tools)})
        if request.method == "tools/call":
            name = str(request.params.get("name", ""))
            args = request.params.get("arguments", {})
            self.calls.append((name, args if isinstance(args, Mapping) else {}))
            return Response(id=request.id, result={"content": [{"type": "text", "text": "ok"}]})
        return Response(id=request.id, error=JsonRpcError(-32601, f"no method {request.method!r}"))

    async def notify(self, request: Request) -> None:
        del request

    async def close(self) -> None:
        self.closed = True

    @property
    def alive(self) -> bool:
        return self.started and not self.closed

    @property
    def detail(self) -> str:
        return "fake transport"


@dataclass(slots=True)
class DeadMcpTransport:
    """A server that cannot be reached at all: ``start`` raises.

    The shape that matters most for the wiring test — a server listed in ``mcp.json``
    whose process is not there must cost a note, not the session.
    """

    reason: str = "connection refused"

    async def start(self) -> None:
        raise TransportClosed(self.reason)

    async def send(self, request: Request) -> Response:
        raise TransportClosed(self.reason)

    async def notify(self, request: Request) -> None:
        raise TransportClosed(self.reason)

    async def close(self) -> None:
        return None

    @property
    def alive(self) -> bool:
        return False

    @property
    def detail(self) -> str:
        return self.reason


def transport_provider(
    by_name: Mapping[str, FakeMcpTransport | DeadMcpTransport],
) -> Callable[[McpServerConfig], Any]:
    """A ``TransportProvider`` that hands back the transport registered per server."""

    def build(config: McpServerConfig) -> Any:
        return by_name[config.name]

    return build


def mcp_config(root: Path, *names: str) -> Path:
    """Write an ``.ronin/mcp.json`` declaring one stdio server per name."""
    body = {
        "mcpServers": {
            name: {
                "transport": "stdio",
                "command": f"{name}-server",
                "danger_level": "read_only",
            }
            for name in names
        }
    }
    return write(root, ".ronin/mcp.json", json.dumps(body, indent=2) + "\n")


# --------------------------------------------------------------------------- #
# Injected `which` and checkpoint runner
# --------------------------------------------------------------------------- #


def which_for(*present: str) -> Callable[[str], str | None]:
    """A ``shutil.which`` that finds exactly ``present`` and nothing else."""
    found = set(present)

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in found else None

    return which


def no_binaries(name: str) -> str | None:
    """A machine with an empty PATH."""
    del name
    return None


def fake_checkpoint_store(root: Path, *, ok: bool = True) -> CheckpointStore:
    """A store whose git calls are answered in-process.

    ``ok=False`` makes every call fail as if ``git`` were absent, which is the branch
    the doctor has to report as a warning rather than a crash.
    """

    async def run(
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del cwd, timeout, env
        if not ok:
            return CommandOutcome(
                argv=tuple(argv),
                exit_code=127,
                output="git: not found",
                failure=CommandFailure.NOT_FOUND,
            )
        return CommandOutcome(argv=tuple(argv), exit_code=0, output="")

    return CheckpointStore(root, run=run)
