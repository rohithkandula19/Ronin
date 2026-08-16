"""``ronin mcp login`` — run the attended OAuth 2.1 flow for a remote MCP server.

The protocol core, the live driver, and the config surface all shipped already; this is the
attended entrypoint that ties them together. It reads ``.ronin/mcp.json``, finds the named
``auth: oauth`` server, and drives :meth:`ronin.mcp.oauth_driver.OAuthDriver.obtain` — which
opens the browser, catches the loopback redirect, exchanges the code, and persists the result
in the OS keyring so a later ``ronin`` session connects without asking again.

Everything impure — the browser, the loopback socket, the HTTPS calls, the keyring — lives
behind the injected ``driver_for`` seam (the real one is
:func:`ronin.mcp.oauth_driver.default_oauth_driver` with ``interactive=True``). The config
loader is injected too, so the resolution and every error path are tested offline against a
fake driver, exactly as :mod:`ronin.cli.repo` injects its scanner.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ronin.mcp.config import AuthKind, ConfigError, McpServerConfig, load_mcp_config
from ronin.mcp.oauth import OAuthError, TokenSet
from ronin.mcp.oauth_driver import default_oauth_driver

#: Where per-server auth state lives when the host has no keyring (see ``default_oauth_driver``).
MCP_AUTH_SUBDIR = Path(".ronin") / "mcp-auth"

#: The subcommands ``ronin mcp`` accepts. Just one for now; the group exists so ``login`` is
#: ``ronin mcp login`` rather than a second top-level verb next to ``mcp-serve``.
SUBCOMMANDS: tuple[str, ...] = ("login",)

class LoginDriver(Protocol):
    """The one thing login needs from the OAuth driver: run the attended flow for a server.

    A Protocol rather than the concrete :class:`~ronin.mcp.oauth_driver.OAuthDriver` so a test
    injects a fake with just this method — login has no business touching the driver's other
    surface, and the seam says so."""

    async def obtain(self, config: McpServerConfig) -> TokenSet: ...


Loader = Callable[[Path, Mapping[str, str]], tuple[McpServerConfig, ...]]
DriverFactory = Callable[[Path], LoginDriver]


@dataclass(frozen=True, slots=True)
class McpLoginOptions:
    """A parsed ``ronin mcp login`` invocation. Pure data; the root is resolved in dispatch."""

    server: str
    root: Path


def _default_load(root: Path, environ: Mapping[str, str]) -> tuple[McpServerConfig, ...]:
    return load_mcp_config(root, environ=environ)


def _default_driver(root: Path) -> LoginDriver:  # pragma: no cover - real browser + keyring
    return default_oauth_driver(root / MCP_AUTH_SUBDIR, interactive=True)


async def run_mcp_login(
    options: McpLoginOptions,
    *,
    environ: Mapping[str, str] | None = None,
    load: Loader = _default_load,
    driver_for: DriverFactory = _default_driver,
) -> tuple[int, str, str]:
    """Run ``ronin mcp login <server>``. Returns ``(exit_code, stdout, stderr)``.

    Fails closed with a message on stderr — never a traceback — for the three things the user
    can get wrong: a malformed config, a server name that is not in it, and a server that does
    not use ``auth: oauth``. A flow that reaches the authorization server but cannot complete
    (the user cancels, the server errors) is a ``1``; a usage problem is a ``2``.
    """
    try:
        configs = load(options.root, environ or {})
    except ConfigError as exc:
        return 2, "", f"ronin mcp login: {exc}\n"

    config = next((c for c in configs if c.name == options.server), None)
    if config is None:
        oauth_names = [c.name for c in configs if c.auth is AuthKind.OAUTH]
        available = ", ".join(oauth_names) if oauth_names else "(none declare auth: oauth)"
        return 2, "", (
            f"ronin mcp login: no server named {options.server!r} in .ronin/mcp.json; "
            f"servers that use OAuth: {available}\n"
        )
    if config.auth is not AuthKind.OAUTH:
        return 2, "", (
            f"ronin mcp login: server {options.server!r} does not use 'auth: oauth' "
            "(it needs no interactive login)\n"
        )

    try:
        token = await driver_for(options.root).obtain(config)
    except (OAuthError, OSError) as exc:
        return 1, "", f"ronin mcp login: authorizing {options.server!r} failed: {exc}\n"

    scopes = " ".join(token.scopes) if token.scopes else "(none advertised)"
    out = (
        f"Authorized {options.server!r} for {token.resource}.\n"
        f"Scopes: {scopes}\n"
        "The token is stored in your OS keyring; later ronin sessions will use it "
        "and refresh it automatically. If this host has no keyring, the login applies "
        "only to the current process.\n"
    )
    return 0, out, ""


__all__ = [
    "MCP_AUTH_SUBDIR",
    "SUBCOMMANDS",
    "DriverFactory",
    "Loader",
    "LoginDriver",
    "McpLoginOptions",
    "run_mcp_login",
]
