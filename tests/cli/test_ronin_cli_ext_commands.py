"""The three new subcommands wired into argv → dispatch: `acp`, `api`, `plugin`.

The protocol servers (ACP, the HTTP API) prove themselves in their own module tests
(`test_ronin_cli_acp.py`, `test_ronin_cli_http_api.py`) — this file owns the *argv seam*:
that each subcommand parses to the right `Options`, refuses what would corrupt its wire,
and — for `plugin`, the one that needs no model — runs end to end through `dispatch`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ronin.cli.main import Command, Options, Streams, dispatch, parse


def captured(answers: list[str] | None = None) -> tuple[Streams, list[str], list[str]]:
    out: list[str] = []
    err: list[str] = []
    scripted = list(answers or [])

    def ask(_question: str) -> str:
        return scripted.pop(0) if scripted else ""

    return (
        Streams(out=out.append, err=err.append, ask=ask, flush=lambda: None, isatty=False),
        out,
        err,
    )


def opts(argv: list[str]) -> Options:
    parsed = parse(argv)
    assert isinstance(parsed, Options), parsed
    return parsed


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #


def test_acp_parses_and_forbids_the_stdout_writers() -> None:
    parsed = opts(["acp"])
    assert parsed.command is Command.ACP
    assert parsed.wizard is False
    for argv in (
        ["acp", "do", "a", "thing"],
        ["acp", "-p", "x"],
        ["acp", "--output-format", "json"],
    ):
        bad = parse(argv)
        assert not isinstance(bad, Options), argv


def test_api_carries_host_and_port_and_rejects_a_bad_port() -> None:
    parsed = opts(["api", "--host", "0.0.0.0", "--port", "9999"])
    assert parsed.command is Command.API
    assert parsed.host == "0.0.0.0"
    assert parsed.port == 9999
    assert not isinstance(parse(["api", "--port", "70000"]), Options)
    assert not isinstance(parse(["api", "serve", "something"]), Options)


def test_api_defaults_to_loopback() -> None:
    parsed = opts(["api"])
    assert parsed.host == "127.0.0.1"
    assert parsed.port == 8080


def test_plugin_parses_add_and_list_and_refuses_the_rest() -> None:
    added = opts(["plugin", "add", "/some/path"])
    assert added.command is Command.PLUGIN
    assert added.plugin_verb == "add"
    assert added.plugin_source == "/some/path"
    assert opts(["plugin", "list"]).plugin_verb == "list"
    assert not isinstance(parse(["plugin"]), Options), "a bare verb is refused"
    assert not isinstance(parse(["plugin", "add"]), Options), "add needs a path"
    url = parse(["plugin", "add", "https://example.com/x.git"])
    assert not isinstance(url, Options)
    assert "local path, not a URL" in url.message


# --------------------------------------------------------------------------- #
# plugin, end to end through dispatch (no model needed)
# --------------------------------------------------------------------------- #


def write_plugin(source_root: Path) -> Path:
    directory = source_root / "greetpack"
    (directory / ".ronin-plugin").mkdir(parents=True)
    (directory / ".ronin-plugin" / "plugin.json").write_text(
        json.dumps({"name": "greetpack", "description": "adds a greeting", "trust": "community"}),
        encoding="utf-8",
    )
    (directory / "commands").mkdir()
    (directory / "commands" / "greet.md").write_text(
        "# greet\nSay hi to $ARGUMENTS\n", encoding="utf-8"
    )
    return directory


async def test_plugin_add_prompts_for_consent_then_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_plugin(tmp_path / "src")
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # dispatch derives paths.home from Path.home(), so $HOME is the install target.
    monkeypatch.setenv("HOME", str(home))
    streams, out, _err = captured(answers=["y"])
    code = await dispatch(
        opts(["plugin", "add", str(source), "--cwd", str(workspace)]), streams=streams, environ={}
    )
    assert code == 0
    assert any("adds a greeting" in line or "wants to" in line for line in out), out
    assert any("installed greetpack" in line for line in out)
    assert (home / ".ronin" / "plugins" / "greetpack").is_dir()


async def test_plugin_add_declined_installs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_plugin(tmp_path / "src")
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    streams, _out, err = captured(answers=["n"])
    code = await dispatch(
        opts(["plugin", "add", str(source), "--cwd", str(workspace)]), streams=streams, environ={}
    )
    assert code == 1
    assert any("declined" in line for line in err)
    assert not (home / ".ronin" / "plugins" / "greetpack").exists()


async def test_plugin_list_reports_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ronin.ext.plugins import install

    home = tmp_path / "home"
    install(write_plugin(tmp_path / "src"), home)  # programmatic, no consent gate
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    streams, out, _err = captured()
    code = await dispatch(
        opts(["plugin", "list", "--cwd", str(workspace)]), streams=streams, environ={}
    )
    assert code == 0
    assert any("greetpack" in line for line in out)
