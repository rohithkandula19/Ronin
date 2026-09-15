"""``ronin mcp`` — read ``.ronin/mcp.json``, and log in to what it declares.

``login`` runs the attended OAuth 2.1 flow. The protocol core, the live driver and the
config surface all shipped already; this is the entrypoint that ties them together. It
reads ``.ronin/mcp.json``, finds the named ``auth: oauth`` server, and drives
:meth:`ronin.mcp.oauth_driver.OAuthDriver.obtain` — which opens the browser, catches the
loopback redirect, exchanges the code, and persists the result in the OS keyring so a later
``ronin`` session connects without asking again.

``list`` reads the same file and reports it. It is the smaller command and the one more
people need: ``mcp.json`` is edited by hand, its own module docstring says "a config error
must be loud at load time or it is invisible forever", and until now the only way to make
it loud was to start a session and read a note. It also prints the **effective** gate for
each server rather than the declared one, because the two differ exactly where it matters —
an undeclared server is gated, and a `DESTRUCTIVE` one is gated whatever the config says.

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

from ronin.mcp.config import (
    AuthKind,
    ConfigError,
    McpServerConfig,
    TransportKind,
    load_mcp_config,
)
from ronin.mcp.oauth import OAuthError, TokenSet
from ronin.mcp.oauth_driver import default_oauth_driver

#: Where per-server auth state lives when the host has no keyring (see ``default_oauth_driver``).
MCP_AUTH_SUBDIR = Path(".ronin") / "mcp-auth"

#: The subcommands ``ronin mcp`` accepts, in help order. The group exists so these are
#: ``ronin mcp <verb>`` rather than more top-level verbs next to ``mcp-serve``.
SUBCOMMANDS: tuple[str, ...] = ("list", "login")


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


@dataclass(frozen=True, slots=True)
class McpListOptions:
    """A parsed ``ronin mcp list``. Pure data; the root is resolved in dispatch."""

    root: Path
    as_json: bool = False


def _describe(config: McpServerConfig) -> list[str]:
    target = config.url if config.transport is not TransportKind.STDIO else " ".join(config.argv)
    lines = [
        f"  {config.name}{'' if config.enabled else '  (disabled)'}",
        f"    transport: {config.transport.value}",
        f"    target:    {target}",
        f"    danger:    {config.effective_danger_level.name.lower()}"
        f"{'' if config.danger_level is not None else '  (undeclared — treated as unknown)'}",
        f"    approval:  {'required' if config.effective_requires_approval else 'waived'}",
    ]
    if config.auth is not AuthKind.NONE:
        lines.append(f"    auth:      {config.auth.value}")
    # Only where the two disagree, and only that way round. A config asking for a
    # waiver and not getting one is the fail-closed rule doing its job silently,
    # and silence is what makes an operator believe the waiver took.
    if config.requires_approval is False and config.effective_requires_approval:
        lines.append(
            "    NOTE:      'requires_approval: false' does not apply — a "
            f"{config.effective_danger_level.name.lower()} server is always gated"
        )
    return lines


def _as_json(configs: tuple[McpServerConfig, ...]) -> str:
    import json

    payload = [
        {
            "name": config.name,
            "transport": config.transport.value,
            "enabled": config.enabled,
            "command": config.command,
            "args": list(config.args),
            "url": config.url,
            "auth": config.auth.value,
            "declared_danger": (
                config.danger_level.name.lower() if config.danger_level is not None else None
            ),
            "effective_danger": config.effective_danger_level.name.lower(),
            "declared_requires_approval": config.requires_approval,
            "effective_requires_approval": config.effective_requires_approval,
            "timeout_seconds": config.timeout_seconds,
        }
        for config in configs
    ]
    return json.dumps(payload, indent=2) + "\n"


def run_mcp_list(
    options: McpListOptions,
    *,
    environ: Mapping[str, str] | None = None,
    load: Loader = _default_load,
) -> tuple[int, str, str]:
    """Run ``ronin mcp list``. Returns ``(exit_code, stdout, stderr)``.

    ``2`` for a config this cannot read, and that is the interesting exit. A
    malformed ``mcp.json`` costs a session every tool it declares, and the failure
    looks from inside the session exactly like a model that chose not to use them.
    Surfacing the loader's own message — which names the offending server and key —
    is the whole point of the verb.

    An empty config is ``0``: most repositories have no servers, and "none" is an
    answer rather than a problem.
    """
    try:
        configs = load(options.root, environ or {})
    except (ConfigError, ValueError, OSError) as exc:
        return 2, "", f"ronin mcp list: {exc}\n"

    if options.as_json:
        return 0, _as_json(configs), ""
    if not configs:
        return 0, "ronin mcp: no servers configured in .ronin/mcp.json\n", ""

    live = sum(1 for config in configs if config.enabled)
    header = (
        f"ronin mcp: {len(configs)} server(s) in .ronin/mcp.json"
        f"{'' if live == len(configs) else f', {live} enabled'}\n"
    )
    body = "\n".join(line for config in configs for line in _describe(config))
    return 0, f"{header}\n{body}\n", ""


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
        return (
            2,
            "",
            (
                f"ronin mcp login: no server named {options.server!r} in .ronin/mcp.json; "
                f"servers that use OAuth: {available}\n"
            ),
        )
    if config.auth is not AuthKind.OAUTH:
        return (
            2,
            "",
            (
                f"ronin mcp login: server {options.server!r} does not use 'auth: oauth' "
                "(it needs no interactive login)\n"
            ),
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
    "McpListOptions",
    "McpLoginOptions",
    "run_mcp_list",
    "run_mcp_login",
]
