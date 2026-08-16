"""``ronin mcp login`` — the attended OAuth entrypoint, resolved offline.

The browser, the loopback socket, the HTTPS round trips and the keyring are all behind the
injected ``driver_for`` seam, so these tests drive the *decision* logic — which server, is it
OAuth, did the flow succeed — against a fake driver and a fake config loader. No socket, no
keyring, no ``.ronin`` written.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ronin.cli.main import Command, Options, Usage, parse
from ronin.cli.mcp_auth import Loader, McpLoginOptions, run_mcp_login
from ronin.mcp.config import AuthKind, ConfigError, McpServerConfig, TransportKind
from ronin.mcp.oauth import OAuthError, TokenSet

RESOURCE = "https://mcp.example.com/mcp"


def _oauth(name: str = "docs") -> McpServerConfig:
    return McpServerConfig(
        name=name, transport=TransportKind.HTTP, url=RESOURCE, auth=AuthKind.OAUTH
    )


def _plain(name: str = "local") -> McpServerConfig:
    return McpServerConfig(name=name, transport=TransportKind.STDIO, command="x")


class _FakeDriver:
    """Records the config it was asked to authorize and returns a canned token."""

    def __init__(self, *, token: TokenSet | None = None, error: Exception | None = None) -> None:
        self._token = token or TokenSet("at", RESOURCE, scopes=("mcp:read",))
        self._error = error
        self.obtained: list[str] = []

    async def obtain(self, config: McpServerConfig) -> TokenSet:
        self.obtained.append(config.name)
        if self._error is not None:
            raise self._error
        return self._token


def _loader(*configs: McpServerConfig) -> Loader:
    def load(root: Path, environ: Mapping[str, str]) -> tuple[McpServerConfig, ...]:
        return tuple(configs)

    return load


async def test_login_runs_the_flow_for_an_oauth_server() -> None:
    driver = _FakeDriver(token=TokenSet("at", RESOURCE, scopes=("mcp:read", "mcp:write")))
    code, out, err = await run_mcp_login(
        McpLoginOptions(server="docs", root=Path(".")),
        load=_loader(_oauth("docs"), _plain("local")),
        driver_for=lambda root: driver,
    )
    assert code == 0 and err == ""
    assert driver.obtained == ["docs"]
    assert "Authorized 'docs'" in out and "mcp:read mcp:write" in out
    assert "keyring" in out.lower()


async def test_login_rejects_an_unknown_server_and_lists_the_oauth_ones() -> None:
    code, out, err = await run_mcp_login(
        McpLoginOptions(server="nope", root=Path(".")),
        load=_loader(_oauth("docs"), _oauth("deploy"), _plain("local")),
        driver_for=lambda root: _FakeDriver(),
    )
    assert code == 2 and out == ""
    assert "no server named 'nope'" in err
    assert "docs" in err and "deploy" in err and "local" not in err  # only the oauth names


async def test_login_refuses_a_non_oauth_server() -> None:
    code, _out, err = await run_mcp_login(
        McpLoginOptions(server="local", root=Path(".")),
        load=_loader(_plain("local")),
        driver_for=lambda root: _FakeDriver(),
    )
    assert code == 2
    assert "does not use 'auth: oauth'" in err


async def test_login_reports_a_config_error_without_a_traceback() -> None:
    def bad_loader(root: Path, environ: Mapping[str, str]) -> tuple[McpServerConfig, ...]:
        raise ConfigError("server 'docs': key 'auth' is 'basic'")

    code, _out, err = await run_mcp_login(
        McpLoginOptions(server="docs", root=Path(".")),
        load=bad_loader,
        driver_for=lambda root: _FakeDriver(),
    )
    assert code == 2 and "key 'auth' is 'basic'" in err


async def test_login_surfaces_a_flow_failure_as_exit_one() -> None:
    driver = _FakeDriver(error=OAuthError("redirect carried no authorization code"))
    code, _out, err = await run_mcp_login(
        McpLoginOptions(server="docs", root=Path(".")),
        load=_loader(_oauth("docs")),
        driver_for=lambda root: driver,
    )
    assert code == 1
    assert "authorizing 'docs' failed" in err and "no authorization code" in err


# --------------------------------------------------------------------------- #
# parsing: ronin mcp login <server>
# --------------------------------------------------------------------------- #


def test_parse_mcp_login_builds_options() -> None:
    parsed = parse(["mcp", "login", "docs"])
    assert isinstance(parsed, Options)
    assert parsed.command is Command.MCP
    assert parsed.mcp_login is not None and parsed.mcp_login.server == "docs"


def test_parse_mcp_needs_a_subcommand() -> None:
    usage = parse(["mcp"])
    assert isinstance(usage, Usage) and "needs a subcommand" in usage.message


def test_parse_mcp_rejects_an_unknown_subcommand() -> None:
    usage = parse(["mcp", "logout"])
    assert isinstance(usage, Usage) and "unknown subcommand" in usage.message


def test_parse_mcp_login_needs_a_server_name() -> None:
    usage = parse(["mcp", "login"])
    assert isinstance(usage, Usage) and "needs a server name" in usage.message
