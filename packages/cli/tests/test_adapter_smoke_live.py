"""LIVE smoke test: Ronin's EmbeddedProvider + the fine-tuned LoRA adapter.

Proves the full integration seam — adapter load, tools rendered via the chat
template, ``<tool_call>`` output parsed into structured ``ToolCall``s — against a
REAL model on real weights. This is the "Ronin can actually run tasks with the
adapter" check, not a mock.

It is skipped (never silently passed) when the environment can't run it:
- ``mlx_lm`` not installed (Linux CI, Intel Macs), or
- no adapter directory (``RONIN_ADAPTER`` unset and the default training output
  absent). Reproduce the adapter from seed 0 with the commands in
  ``training/reports/finetune_comparison.md``.

Opt-in (loads ~1 GB of weights — never part of a routine test run):
  RONIN_ADAPTER_SMOKE=1 uv run pytest packages/cli/tests/test_adapter_smoke_live.py -q
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.adapter_smoke,
    pytest.mark.skipif(
        os.environ.get("RONIN_ADAPTER_SMOKE") != "1",
        reason="live model smoke is opt-in: set RONIN_ADAPTER_SMOKE=1",
    ),
]

_REPO = Path(__file__).resolve().parents[3]
_DEFAULT_ADAPTER = _REPO / "training" / "adapters" / "ronin_1.5b_iter150"


def _adapter_dir() -> Path | None:
    env = os.environ.get("RONIN_ADAPTER", "").strip()
    if env:
        return Path(env)
    if (_DEFAULT_ADAPTER / "adapters.safetensors").is_file():
        return _DEFAULT_ADAPTER
    return None


mlx_lm = pytest.importorskip(
    "mlx_lm", reason="mlx-lm not installed (Apple Silicon only): pip install ronin-cli[local]"
)


@pytest.fixture(scope="module")
def provider():
    adapter = _adapter_dir()
    if adapter is None:
        pytest.skip(
            "no trained adapter found — set RONIN_ADAPTER or reproduce it from "
            "seed 0 (see training/reports/finetune_comparison.md)"
        )
    from ronin_cli.embedded_provider import EmbeddedProvider

    return EmbeddedProvider(
        model="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit",
        adapter_path=str(adapter),
    )


def test_adapter_loads_and_generates(provider) -> None:
    from ronin_agent_patterns.providers.base import Message

    resp = provider.complete(
        system="You are Ronin, a coding agent.",
        messages=[Message(role="user", content="Say READY and nothing else.")],
        tools=[],
        max_tokens=16,
    )
    assert isinstance(resp.text, str) and resp.text.strip()


def test_adapter_tool_output_is_parseable(provider) -> None:
    """The protocol claim: whatever the adapter emits for a tool-shaped task must
    be either (a) structured calls the runtime parsed, or (b) plain text with NO
    unparsed <tool_call> debris — i.e. nothing the runtime had to throw away.
    This asserts parseability of the protocol, not task success."""
    from ronin_agent_patterns.providers.base import Message
    from ronin_cli.code_tools import build_code_tools

    resp = provider.complete(
        system="You are Ronin, a coding agent. Use tools when a task needs them.",
        messages=[Message(role="user", content="Read the file pyproject.toml")],
        tools=build_code_tools(str(_REPO)),
        max_tokens=192,
    )
    for call in resp.tool_calls:  # every parsed call is registry-real + dict-args
        assert isinstance(call.arguments, dict)
        assert call.name  # non-empty tool name
    assert "<tool_call>" not in resp.text, (
        "unparsed <tool_call> debris left in text — runtime/model format mismatch"
    )
