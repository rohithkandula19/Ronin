"""``ronin mcp list`` — what the config declares, and what it actually gets.

`.ronin/mcp.json` is edited by hand and `ronin.mcp.config`'s own docstring says a config
error "must be loud at load time or it is invisible forever" — because a typo produces a
server that silently contributes no tools, and from inside a running session that is
indistinguishable from a model that chose not to use them. Until this verb existed the only
way to make it loud was to start a session and read a note.

So the tests that matter are the two where **declared and effective disagree**, which is
exactly where an operator's belief about their config is wrong:

* an undeclared server is gated, because undeclared means unknown and unknown fails closed;
* a `DESTRUCTIVE` server is gated *whatever* `requires_approval` says, and the report says
  so by name rather than letting the waiver look like it took.

The loader is injected, so a malformed config is a string rather than a file, and no
`.ronin/` is written anywhere.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from ronin.cli.main import Command, Options, Usage, parse
from ronin.cli.mcp_auth import Loader, McpListOptions, run_mcp_list
from ronin.core.types import DangerLevel
from ronin.mcp.config import AuthKind, ConfigError, McpServerConfig, TransportKind

ROOT = Path("/workspace")


def _loader(*configs: McpServerConfig) -> Loader:
    def load(_root: Path, _environ: Mapping[str, str]) -> tuple[McpServerConfig, ...]:
        return configs

    return load


def _raiser(error: Exception) -> Loader:
    def load(_root: Path, _environ: Mapping[str, str]) -> tuple[McpServerConfig, ...]:
        raise error

    return load


def _stdio(name: str = "docs", **extra: Any) -> McpServerConfig:
    return McpServerConfig(name=name, transport=TransportKind.STDIO, command="npx", **extra)


def _run(*configs: McpServerConfig, as_json: bool = False) -> tuple[int, str, str]:
    return run_mcp_list(McpListOptions(root=ROOT, as_json=as_json), load=_loader(*configs))


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #


def test_a_configured_server_is_reported_with_its_transport_and_target() -> None:
    code, out, err = _run(_stdio(args=("-y", "@scope/docs")))
    assert (code, err) == (0, "")
    assert "docs" in out
    assert "transport: stdio" in out
    assert "npx -y @scope/docs" in out


def test_a_network_server_reports_its_url_rather_than_an_empty_argv() -> None:
    code, out, _err = _run(
        McpServerConfig(name="deploy", transport=TransportKind.HTTP, url="https://mcp/rpc")
    )
    assert code == 0
    assert "https://mcp/rpc" in out


def test_no_servers_is_an_answer_not_a_problem() -> None:
    """Most repositories have none, so this must not read as a failure."""
    code, out, err = _run()
    assert (code, err) == (0, "")
    assert "no servers configured" in out


def test_a_disabled_server_is_listed_and_marked() -> None:
    """Omitting it would make `enabled: false` indistinguishable from a typo'd name."""
    code, out, _err = _run(_stdio("docs"), _stdio("off", enabled=False))
    assert code == 0
    assert "(disabled)" in out
    assert "2 server(s)" in out and "1 enabled" in out


def test_the_count_does_not_claim_a_subset_when_every_server_is_enabled() -> None:
    _code, out, _err = _run(_stdio("a"), _stdio("b"))
    assert "2 server(s) in .ronin/mcp.json\n" in out


# --------------------------------------------------------------------------- #
# where declared and effective disagree — the whole point of the verb
# --------------------------------------------------------------------------- #


def test_an_undeclared_server_is_reported_as_gated_and_as_undeclared() -> None:
    """Both halves matter. "Gated" is what happens; "undeclared" is why, and without
    it the operator reads a deliberate choice they did not make."""
    _code, out, _err = _run(_stdio())
    assert "approval:  required" in out
    assert "undeclared" in out


def test_a_waiver_that_does_not_apply_is_named_rather_than_shown_as_applied() -> None:
    """The report an operator most needs to see.

    `requires_approval: false` on a DESTRUCTIVE server is overridden — `ToolSpec` would
    refuse to construct otherwise. The config keeps the key, so reading the file suggests
    the waiver took. Silence here is what would let somebody believe it.
    """
    _code, out, _err = _run(_stdio(danger_level=DangerLevel.DESTRUCTIVE, requires_approval=False))
    assert "approval:  required" in out
    assert "does not apply" in out
    assert "destructive" in out


def test_a_waiver_that_does_apply_is_shown_as_waived_with_no_note() -> None:
    """The note is only for disagreement — on every server it would be noise, and noise
    is how a line that matters stops being read."""
    _code, out, _err = _run(_stdio(danger_level=DangerLevel.READ_ONLY, requires_approval=False))
    assert "approval:  waived" in out
    assert "does not apply" not in out


def test_a_gated_server_that_asked_for_nothing_gets_no_note_either() -> None:
    _code, out, _err = _run(_stdio(danger_level=DangerLevel.READ_ONLY))
    assert "approval:  required" in out
    assert "does not apply" not in out


def test_auth_is_shown_only_where_there_is_any() -> None:
    plain = _run(_stdio())[1]
    assert "auth:" not in plain

    oauth = _run(
        McpServerConfig(
            name="docs",
            transport=TransportKind.HTTP,
            url="https://mcp/rpc",
            auth=AuthKind.OAUTH,
        )
    )[1]
    assert "auth:      oauth" in oauth


# --------------------------------------------------------------------------- #
# a config that cannot be read
# --------------------------------------------------------------------------- #


def test_a_malformed_config_surfaces_the_loaders_own_message() -> None:
    """Which names the offending server and key. Re-wording it here would lose the
    position, and the position is the actionable part when the file holds several."""
    code, out, err = run_mcp_list(
        McpListOptions(root=ROOT),
        load=_raiser(ConfigError("server 'bad': missing required key 'command'")),
    )
    assert code == 2
    assert out == ""
    assert "server 'bad': missing required key 'command'" in err


def test_an_invariant_the_loader_does_not_rewrap_is_still_a_message() -> None:
    """`McpServerConfig.__post_init__` raises bare `ValueError` for a handful of rules.
    `wire.py` catches `(McpError, ValueError, OSError)` for exactly this reason, and a
    verb that caught only `ConfigError` would traceback on a real config."""
    code, _out, err = run_mcp_list(
        McpListOptions(root=ROOT),
        load=_raiser(ValueError("server 'x': a stdio server needs a command")),
    )
    assert code == 2
    assert "a stdio server needs a command" in err


def test_an_unreadable_file_is_a_message_too() -> None:
    code, _out, err = run_mcp_list(
        McpListOptions(root=ROOT), load=_raiser(OSError("permission denied"))
    )
    assert code == 2
    assert "permission denied" in err


def test_a_broken_config_exits_two_and_an_empty_one_exits_zero() -> None:
    """The distinction the exit code carries: "could not read it" is not "there is
    nothing in it". A wrapper that treated both as 0 would report a healthy MCP setup
    for a config that contributes no tools at all."""
    assert _run()[0] == 0
    assert run_mcp_list(McpListOptions(root=ROOT), load=_raiser(ConfigError("x")))[0] == 2


# --------------------------------------------------------------------------- #
# json
# --------------------------------------------------------------------------- #


def test_json_carries_declared_and_effective_separately() -> None:
    """Both, not one. A tool reading this has to be able to tell "nobody said" from
    "somebody said read_only", which is the same distinction the dataclass keeps."""
    _code, out, _err = _run(
        _stdio("mystery"),
        _stdio("safe", danger_level=DangerLevel.READ_ONLY, requires_approval=False),
        as_json=True,
    )
    payload = json.loads(out)

    mystery, safe = payload
    assert mystery["declared_danger"] is None
    assert mystery["effective_danger"] == "mutating"
    assert mystery["declared_requires_approval"] is None
    assert mystery["effective_requires_approval"] is True

    assert safe["declared_danger"] == "read_only"
    assert safe["effective_requires_approval"] is False


def test_json_of_an_empty_config_is_an_empty_list_not_prose() -> None:
    _code, out, _err = _run(as_json=True)
    assert json.loads(out) == []


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #


def test_mcp_list_parses(tmp_path: Path) -> None:
    options = parse(["mcp", "list", "--cwd", str(tmp_path)])
    assert isinstance(options, Options)
    assert options.command is Command.MCP
    assert options.mcp_list == McpListOptions(root=tmp_path)
    assert options.mcp_login is None, "the two must not both be set"


def test_mcp_login_still_parses_and_does_not_set_the_list_options(tmp_path: Path) -> None:
    options = parse(["mcp", "login", "docs", "--cwd", str(tmp_path)])
    assert isinstance(options, Options)
    assert options.mcp_login is not None and options.mcp_login.server == "docs"
    assert options.mcp_list is None


def test_output_format_json_reaches_the_list_options(tmp_path: Path) -> None:
    options = parse(["mcp", "list", "--output-format", "json", "--cwd", str(tmp_path)])
    assert isinstance(options, Options)
    assert options.mcp_list is not None and options.mcp_list.as_json


def test_a_server_name_after_list_is_refused_rather_than_ignored() -> None:
    """`ronin mcp list docs` reads like it filters. Listing everything instead is a
    correct-looking answer to a question nobody asked."""
    refused = parse(["mcp", "list", "docs"])
    assert isinstance(refused, Usage)
    assert "takes no arguments" in refused.message


def test_an_unknown_subcommand_lists_the_real_ones() -> None:
    refused = parse(["mcp", "lsit"])
    assert isinstance(refused, Usage)
    assert "list, login" in refused.message


def test_mcp_with_no_subcommand_lists_them() -> None:
    refused = parse(["mcp"])
    assert isinstance(refused, Usage)
    assert "list, login" in refused.message


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
