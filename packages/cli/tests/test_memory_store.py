from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from ronin_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ronin_cli import code_mode, memory_store
from ronin_cli.config import RoninConfig
from ronin_cli.main import app

runner = CliRunner()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_add_and_load(home: Path) -> None:
    assert memory_store.load_memories() == []
    assert memory_store.add_memory("My name is Rohith") is True
    assert memory_store.add_memory("I use Groq") is True
    texts = [m["text"] for m in memory_store.load_memories()]
    assert texts == ["My name is Rohith", "I use Groq"]
    # persisted to ~/.csk/memory.json
    assert (home / ".csk" / "memory.json").is_file()


def test_dedupe_and_blank(home: Path) -> None:
    assert memory_store.add_memory("I prefer Python") is True
    assert memory_store.add_memory("i prefer python") is False  # case-insensitive dup
    assert memory_store.add_memory("   ") is False
    assert len(memory_store.load_memories()) == 1


def test_prompt_block(home: Path) -> None:
    assert memory_store.memory_prompt_block() == ""
    memory_store.add_memory("Works on a project called ronin")
    block = memory_store.memory_prompt_block()
    assert "What you remember about the user" in block
    assert "ronin" in block


def test_remember_tool(home: Path) -> None:
    tool = memory_store.build_remember_tool()
    assert tool.name == "remember"
    msg = tool.handler(fact="Lives in the terminal")
    assert "long-term memory" in msg
    assert memory_store.load_memories()[0]["text"] == "Lives in the terminal"


def test_forget_all(home: Path) -> None:
    memory_store.add_memory("a"); memory_store.add_memory("b")
    assert memory_store.forget_all() == 2
    assert memory_store.load_memories() == []


# ---------- CLI ----------

def test_cli_memory_add_list_clear(home: Path) -> None:
    r = runner.invoke(app, ["memory", "--add", "I prefer tabs"])
    assert r.exit_code == 0 and "remembered" in r.stdout
    r = runner.invoke(app, ["memory"])
    assert "I prefer tabs" in r.stdout and "remembers about you" in r.stdout
    r = runner.invoke(app, ["memory", "--clear"])
    assert "forgot 1" in r.stdout
    r = runner.invoke(app, ["memory"])
    assert "no memories yet" in r.stdout


# ---------- the agent saves across sessions ----------

def test_unified_session_persists_memory(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic", demo_mode=True)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    it = iter(["my name is Rohith and I work in ~/ronin", "/q"])
    console.input = lambda *a, **k: next(it)  # type: ignore[assignment]

    provider = FakeProvider(responses=[
        LLMResponse(text="Got it.", tool_calls=[ToolCall(id="t1", name="remember",
                    arguments={"fact": "User's name is Rohith; works in ~/ronin"})],
                    stop_reason="tool_use", usage={}),
        LLMResponse(text="I'll remember that.", stop_reason="end_turn", usage={}),
    ])
    with patch("ronin_cli.code_mode.build_provider", return_value=provider):
        code_mode.run_unified_session(config, root=home, console=console)

    # the fact persists to disk for the NEXT session
    texts = [m["text"] for m in memory_store.load_memories()]
    assert any("Rohith" in t for t in texts)


# ---------- automatic extraction (remembers everything) ----------

def test_parse_json_list() -> None:
    assert memory_store._parse_json_list('here: ["a", "b"] done') == ["a", "b"]
    assert memory_store._parse_json_list("no json") == []
    assert memory_store._parse_json_list('[]') == []


def test_auto_extract_saves_facts(home: Path) -> None:
    from ronin_agent_patterns import FakeProvider, LLMResponse
    fake = FakeProvider(responses=[LLMResponse(
        text='["User\'s name is Rohith", "User uses Groq"]', stop_reason="end_turn", usage={})])
    with patch("ronin_cli.runner.build_provider", return_value=fake):
        n = memory_store.auto_extract(RoninConfig(provider="anthropic"),
                                      "USER: hi I'm Rohith and I use Groq\nASSISTANT: hello")
    assert n == 2
    texts = [m["text"] for m in memory_store.load_memories()]
    assert "User's name is Rohith" in texts and "User uses Groq" in texts


def test_auto_extract_silent_on_error(home: Path) -> None:
    class Boom:
        model = "x"
        def complete(self, **kw): raise RuntimeError("rate limited")
    with patch("ronin_cli.runner.build_provider", return_value=Boom()):
        n = memory_store.auto_extract(RoninConfig(provider="groq"), "USER: x\nASSISTANT: y")
    assert n == 0  # never raises, just returns 0
    assert memory_store.load_memories() == []


def test_auto_extract_empty_list(home: Path) -> None:
    from ronin_agent_patterns import FakeProvider, LLMResponse
    fake = FakeProvider(responses=[LLMResponse(text="[]", stop_reason="end_turn", usage={})])
    with patch("ronin_cli.runner.build_provider", return_value=fake):
        n = memory_store.auto_extract(RoninConfig(provider="anthropic"), "USER: what time is it\nASSISTANT: ...")
    assert n == 0
