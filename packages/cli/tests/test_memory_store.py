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
    # persisted to ~/.ronin/memory.json
    assert (home / ".ronin" / "memory.json").is_file()


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


# ---------- per-fact ids + forget_one ----------

def test_each_fact_gets_a_stable_id(home: Path) -> None:
    memory_store.add_memory("I use Groq")
    memory_store.add_memory("I prefer Python")
    mems = memory_store.load_memories()
    ids = [m["id"] for m in mems]
    assert all(ids) and len(set(ids)) == 2          # present and unique
    assert memory_store.load_memories()[0]["id"] == ids[0]  # stable across loads


def test_legacy_records_without_ids_are_backfilled(home: Path) -> None:
    # Simulate an old store written before ids existed.
    memory_store._save([{"text": "old fact", "ts": 1.0}])  # no id
    mems = memory_store.load_memories()
    assert mems[0]["id"]                             # backfilled
    # and the backfill was persisted, so the handle is stable across reloads
    reloaded = memory_store.load_memories()
    assert reloaded[0]["id"] == mems[0]["id"]


def test_forget_one_by_id_position_and_substring(home: Path) -> None:
    memory_store.add_memory("My name is Rohith")
    memory_store.add_memory("I use Groq")
    memory_store.add_memory("I live in the terminal")
    fid = memory_store.load_memories()[1]["id"]

    assert memory_store.forget_one(fid) == "I use Groq"        # by id
    assert memory_store.forget_one("1") == "My name is Rohith"  # by 1-based position
    assert memory_store.forget_one("terminal") == "I live in the terminal"  # by substring
    assert memory_store.load_memories() == []


def test_forget_one_ambiguous_or_missing_returns_none(home: Path) -> None:
    memory_store.add_memory("I like apples")
    memory_store.add_memory("I like apricots")
    assert memory_store.forget_one("nope") is None       # no match
    assert memory_store.forget_one("I like") is None     # ambiguous substring -> no-op
    assert len(memory_store.load_memories()) == 2         # nothing removed
    assert memory_store.forget_one("") is None


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


def test_cli_memory_add_list_forget_verbs(home: Path) -> None:
    r = runner.invoke(app, ["memory", "add", "I", "prefer", "Python"])
    assert r.exit_code == 0 and "remembered" in r.stdout
    runner.invoke(app, ["memory", "add", "I use Groq"])

    r = runner.invoke(app, ["memory", "list"])
    assert "I prefer Python" in r.stdout and "I use Groq" in r.stdout

    # forget by 1-based position
    r = runner.invoke(app, ["memory", "forget", "1"])
    assert r.exit_code == 0 and "forgot: I prefer Python" in r.stdout

    # forget the rest
    r = runner.invoke(app, ["memory", "forget", "--all"])
    assert "forgot 1" in r.stdout
    r = runner.invoke(app, ["memory", "list"])
    assert "no memories yet" in r.stdout


def test_cli_memory_forget_no_match_exits_nonzero(home: Path) -> None:
    runner.invoke(app, ["memory", "add", "something"])
    r = runner.invoke(app, ["memory", "forget", "zzz-nope"])
    assert r.exit_code == 1
    assert len(memory_store.load_memories()) == 1


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
