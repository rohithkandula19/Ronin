"""Shared fixtures for the tool tests: a real temp directory, and no network.

Every test here runs against an actual filesystem in a ``tmp_path``, because the
tools' whole job is touching real files and a mocked filesystem would test the mock.
Nothing reaches the developer's tree: ``ToolContext.root`` is the temp directory and
the tools refuse paths outside it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ronin.core.types import ToolResult, ToolUse
from ronin.tools import Tool, ToolContext
from ronin.tools.task import SubagentType


def context(root: Path, *, vision: bool = False) -> ToolContext:
    """A fresh context rooted at ``root``, with a clean environment."""
    return ToolContext(
        root=root,
        vision=vision,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(root), "LANG": "C.UTF-8"},
    )


async def call(tool: Tool, ctx: ToolContext, **args: Any) -> ToolResult:
    """Invoke a tool through ``execute``, so the error boundary is exercised."""
    return await tool.execute(args, ctx)


def write_file(root: Path, name: str, content: str) -> Path:
    """Create a file under ``root``, making parents as needed."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def use(name: str, **args: Any) -> ToolUse:
    return ToolUse(id=f"call_{name}", name=name, arguments=args)


class RecordingRunner:
    """A subagent runner that records what it was asked and replies from a script."""

    def __init__(self, reply: str = "the subagent's conclusion") -> None:
        self.reply = reply
        self.calls: list[tuple[str, SubagentType]] = []

    async def __call__(self, prompt: str, subagent: SubagentType) -> str:
        self.calls.append((prompt, subagent))
        return self.reply


class FakeNet:
    """Injected network: a page map, a scripted extractor, and a search stub."""

    def __init__(
        self,
        pages: Mapping[str, tuple[str, str]] | None = None,
        *,
        answer: str = "the extracted answer",
        results: Sequence[Mapping[str, str]] = (),
    ) -> None:
        self.pages = dict(pages or {})
        self.answer = answer
        self.results = list(results)
        self.fetches: list[str] = []
        self.extractions: list[tuple[str, str]] = []
        self.searches: list[str] = []
        self.now = 1000.0

    async def fetch(self, url: str) -> tuple[str, str]:
        self.fetches.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected fetch of {url!r}")
        return self.pages[url]

    async def extract(self, prompt: str, text: str) -> str:
        self.extractions.append((prompt, text))
        return self.answer

    async def search(self, query: str) -> Sequence[Mapping[str, str]]:
        self.searches.append(query)
        return self.results

    def clock(self) -> float:
        return self.now
