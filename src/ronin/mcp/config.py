"""``.ronin/mcp.json``: which MCP servers exist, and how much they are trusted.

Validation is strict and every message names the offending **server and key**.
That is not pedantry: an MCP config is edited by hand, a typo in it produces a
server that silently contributes no tools, and "no tools appeared" is
indistinguishable from "the model chose not to use them" once a session is
running. A config error must be loud at load time or it is invisible forever.

The trust half of this file matters more than the parsing half. Two rules:

1. **A server whose danger is not declared is treated as needing approval.**
   Undeclared means unknown, unknown means fail closed. There is no code path
   that turns an undeclared remote tool into an un-gated one.
2. **Opting out of the gate takes two explicit keys.** ``requires_approval:
   false`` without a declared ``danger_level`` is a config *error*, not a
   permission — because the operator who wrote it was reasoning about a tool
   whose danger they had not stated.

Shape::

    {
      "mcpServers": {
        "docs": {
          "transport": "stdio",
          "command": "npx", "args": ["-y", "@scope/server"],
          "env": {"TOKEN": "${DOCS_TOKEN}"},
          "danger_level": "read_only", "requires_approval": false
        },
        "deploy": {"transport": "http", "url": "https://mcp.internal/rpc"}
      }
    }
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.types import DangerLevel
from .jsonrpc import McpError
from .transport import DEFAULT_TIMEOUT_SECONDS

#: Where a repo declares its servers, relative to the project root.
MCP_CONFIG_RELATIVE_PATH = Path(".ronin") / "mcp.json"

#: The only top-level key. Matches the spelling the wider ecosystem uses, so a
#: config can be copied between clients without a rewrite.
SERVERS_KEY = "mcpServers"

#: What a server is assumed to be able to do when it does not say. Deliberately
#: not ``READ_ONLY``: a remote tool named ``search`` may well delete something,
#: and the cost of a needless approval prompt is a keystroke.
UNDECLARED_DANGER_LEVEL = DangerLevel.MUTATING


class ConfigError(McpError):
    """A malformed ``mcp.json``. The message names the server and the key."""


class TransportKind(StrEnum):
    """Which framing a server speaks."""

    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


class AuthKind(StrEnum):
    """How a remote server is authenticated.

    ``NONE`` is the default and covers both an open server and one that takes a static
    bearer via ``headers`` — the config already carries that, and nothing new is needed for
    it. ``OAUTH`` opts a server into the OAuth 2.1 flow in :mod:`ronin.mcp.oauth_driver`:
    the ``Authorization`` header is *resolved* (discovered, obtained, refreshed) rather than
    written by hand. It is only meaningful for a network transport, so it lives among the
    network keys and a stdio server that declares it is rejected.
    """

    NONE = "none"
    OAUTH = "oauth"


_COMMON_KEYS = frozenset(
    {"transport", "danger_level", "requires_approval", "enabled", "timeout_seconds"}
)
_STDIO_KEYS = frozenset({"command", "args", "env", "cwd"})
_NETWORK_KEYS = frozenset({"url", "headers", "auth"})

_AUTH_BY_NAME: Mapping[str, AuthKind] = {kind.value: kind for kind in AuthKind}

_DANGER_BY_NAME: Mapping[str, DangerLevel] = {level.name.lower(): level for level in DangerLevel}


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """One server, validated.

    ``danger_level`` and ``requires_approval`` are ``None`` for "not declared",
    which is a different thing from ``READ_ONLY`` / ``False`` and is why they are
    optional rather than defaulted. Read the effective values off
    :attr:`effective_danger_level` and :attr:`effective_requires_approval`; the
    raw fields exist so a UI can say "this was never declared".
    """

    name: str
    transport: TransportKind
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    danger_level: DangerLevel | None = None
    requires_approval: bool | None = None
    enabled: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    auth: AuthKind = AuthKind.NONE

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("McpServerConfig.name is required")
        if self.transport is TransportKind.STDIO and not self.command:
            raise ValueError(f"server {self.name!r}: a stdio server needs a command")
        if self.transport is not TransportKind.STDIO and not self.url:
            raise ValueError(f"server {self.name!r}: a {self.transport.value} server needs a url")
        if self.auth is AuthKind.OAUTH and self.transport is TransportKind.STDIO:
            raise ValueError(
                f"server {self.name!r}: OAuth applies to network transports, not stdio"
            )
        if self.auth is AuthKind.OAUTH and not _is_secure_url(self.url):
            raise ValueError(
                f"server {self.name!r}: OAuth requires an https url (loopback http excepted)"
            )
        if self.timeout_seconds <= 0:
            raise ValueError(f"server {self.name!r}: timeout_seconds must be positive")

    @property
    def effective_danger_level(self) -> DangerLevel:
        """The level to stamp on this server's tools. Never below what was declared."""
        return self.danger_level if self.danger_level is not None else UNDECLARED_DANGER_LEVEL

    @property
    def effective_requires_approval(self) -> bool:
        """Whether this server's tools are gated. ``True`` unless explicitly waived.

        The single place the fail-closed rule is expressed. An undeclared server
        is gated; a declared one is gated unless the config says otherwise; and a
        ``DESTRUCTIVE``-or-worse server is gated no matter what the config says
        (``ToolSpec`` would refuse to construct otherwise, and duplicating the
        check here means the refusal is a config error rather than a crash).
        """
        if self.effective_danger_level >= DangerLevel.DESTRUCTIVE:
            return True
        if self.requires_approval is None:
            return True
        return self.requires_approval

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.command, *self.args)


def parse_mcp_config(
    data: Mapping[str, Any], *, environ: Mapping[str, str] | None = None
) -> tuple[McpServerConfig, ...]:
    """Validate a parsed ``mcp.json`` body into configs, in declaration order.

    ``environ`` is the mapping ``${VAR}`` references expand against. It is
    injected rather than read from the process, so a test cannot accidentally
    depend on the developer's shell, and an unset variable is an error naming the
    server and key rather than an empty token sent to a remote server.
    """
    if not isinstance(data, Mapping):
        raise ConfigError(f"{SERVERS_KEY}: the config root must be a JSON object")
    unknown = sorted(set(data) - {SERVERS_KEY})
    if unknown:
        raise ConfigError(f"unknown top-level key(s) {unknown}; the only one is {SERVERS_KEY!r}")
    servers = data.get(SERVERS_KEY, {})
    if not isinstance(servers, Mapping):
        raise ConfigError(f"{SERVERS_KEY} must be an object of name -> server config")
    return tuple(_server(str(name), body, environ or {}) for name, body in servers.items())


def load_mcp_config(
    root: Path, *, environ: Mapping[str, str] | None = None
) -> tuple[McpServerConfig, ...]:
    """Read ``<root>/.ronin/mcp.json``. A missing file means no servers.

    Missing is not an error — most repos have none — but *unreadable* and
    *malformed* both are, because those are the cases where the operator believes
    they configured something.
    """
    path = root / MCP_CONFIG_RELATIVE_PATH
    if not path.exists():
        return ()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path} could not be read: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path}: the config root must be a JSON object")
    return parse_mcp_config(data, environ=environ)


def enabled_servers(configs: tuple[McpServerConfig, ...]) -> tuple[McpServerConfig, ...]:
    """The subset a session should connect to."""
    return tuple(config for config in configs if config.enabled)


# --------------------------------------------------------------------------- #
# Field-by-field validation
# --------------------------------------------------------------------------- #


def _server(name: str, body: object, environ: Mapping[str, str]) -> McpServerConfig:
    if not name:
        raise ConfigError(f"{SERVERS_KEY}: a server name cannot be empty")
    if "__" in name:
        # The namespace separator. A server called `a__b` would make
        # `mcp__a__b__tool` ambiguous, which is exactly the collision the naming
        # scheme exists to make impossible.
        raise ConfigError(
            f"server {name!r}: a server name may not contain '__' — it is the "
            "separator in mcp__<server>__<tool> and would make the name ambiguous"
        )
    if not isinstance(body, Mapping):
        raise ConfigError(f"server {name!r}: must be an object, got {type(body).__name__}")

    kind = _transport_kind(name, body)
    allowed = _COMMON_KEYS | (_STDIO_KEYS if kind is TransportKind.STDIO else _NETWORK_KEYS)
    for key in body:
        if key not in allowed:
            hint = (
                f" ({key!r} belongs to a different transport)"
                if key in (_STDIO_KEYS | _NETWORK_KEYS)
                else ""
            )
            raise ConfigError(
                f"server {name!r}: unknown key {key!r} for transport "
                f"{kind.value!r}{hint}; allowed: {sorted(allowed)}"
            )

    danger = _danger(name, body)
    approval = _optional_bool(name, body, "requires_approval")
    if danger is None and approval is False:
        raise ConfigError(
            f"server {name!r}: requires_approval=false without a declared "
            "danger_level. A remote tool of unknown danger is treated as needing "
            "approval; declare danger_level explicitly to waive the gate."
        )

    common: dict[str, Any] = {
        "name": name,
        "transport": kind,
        "danger_level": danger,
        "requires_approval": approval,
        "enabled": _optional_bool(name, body, "enabled") is not False,
        "timeout_seconds": _timeout(name, body),
    }
    if kind is TransportKind.STDIO:
        return McpServerConfig(
            command=_required_str(name, body, "command"),
            args=_string_list(name, body, "args"),
            env=_string_map(name, body, "env", environ),
            cwd=_optional_str(name, body, "cwd"),
            **common,
        )
    url = _required_str(name, body, "url")
    auth = _auth(name, body)
    if auth is AuthKind.OAUTH and not _is_secure_url(url):
        raise ConfigError(
            f"server {name!r}: auth 'oauth' requires an https url (loopback http excepted), "
            f"got {url!r}"
        )
    return McpServerConfig(
        url=url,
        headers=_string_map(name, body, "headers", environ),
        auth=auth,
        **common,
    )


def _transport_kind(name: str, body: Mapping[str, Any]) -> TransportKind:
    declared = body.get("transport")
    if declared is None:
        if "command" in body:
            # Unambiguous: only stdio spawns a process.
            return TransportKind.STDIO
        raise ConfigError(
            f"server {name!r}: missing key 'transport'. A url alone does not say "
            "whether the server speaks 'sse' or 'http', so it must be declared."
        )
    if not isinstance(declared, str):
        raise ConfigError(
            f"server {name!r}: key 'transport' must be a string, got {type(declared).__name__}"
        )
    try:
        return TransportKind(declared)
    except ValueError as exc:
        raise ConfigError(
            f"server {name!r}: key 'transport' is {declared!r}; expected one of "
            f"{[kind.value for kind in TransportKind]}"
        ) from exc


def _auth(name: str, body: Mapping[str, Any]) -> AuthKind:
    raw = body.get("auth")
    if raw is None:
        return AuthKind.NONE
    if not isinstance(raw, str):
        raise ConfigError(f"server {name!r}: key 'auth' must be a string, got {type(raw).__name__}")
    kind = _AUTH_BY_NAME.get(raw.strip().lower())
    if kind is None:
        raise ConfigError(
            f"server {name!r}: key 'auth' is {raw!r}; expected one of {sorted(_AUTH_BY_NAME)}"
        )
    return kind


def _is_secure_url(url: str) -> bool:
    """Whether ``url`` may carry OAuth: https, or http only to a loopback host.

    Same rule the OAuth core applies to endpoints, kept here so a misconfigured server URL
    is refused at config-load time (naming the server) rather than deep in the flow.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and host in ("localhost", "127.0.0.1", "::1")


def _danger(name: str, body: Mapping[str, Any]) -> DangerLevel | None:
    raw = body.get("danger_level")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ConfigError(
            f"server {name!r}: key 'danger_level' must be a string, got {type(raw).__name__}"
        )
    level = _DANGER_BY_NAME.get(raw.strip().lower())
    if level is None:
        raise ConfigError(
            f"server {name!r}: key 'danger_level' is {raw!r}; expected one of "
            f"{sorted(_DANGER_BY_NAME)}"
        )
    return level


def _required_str(name: str, body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if value is None:
        raise ConfigError(f"server {name!r}: missing required key {key!r}")
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"server {name!r}: key {key!r} must be a non-empty string")
    return value


def _optional_str(name: str, body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError(
            f"server {name!r}: key {key!r} must be a string, got {type(value).__name__}"
        )
    return value


def _optional_bool(name: str, body: Mapping[str, Any], key: str) -> bool | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConfigError(f"server {name!r}: key {key!r} must be true or false, got {value!r}")
    return value


def _timeout(name: str, body: Mapping[str, Any]) -> float:
    value = body.get("timeout_seconds")
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"server {name!r}: key 'timeout_seconds' must be a number, got {value!r}")
    if value <= 0:
        raise ConfigError(f"server {name!r}: key 'timeout_seconds' must be positive, got {value!r}")
    return float(value)


def _string_list(name: str, body: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = body.get(key)
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ConfigError(
            f"server {name!r}: key {key!r} must be a list of strings, got {type(value).__name__}"
        )
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(
                f"server {name!r}: key {key!r}[{index}] must be a string, got {type(item).__name__}"
            )
    return tuple(value)


def _string_map(
    name: str, body: Mapping[str, Any], key: str, environ: Mapping[str, str]
) -> Mapping[str, str]:
    value = body.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(
            f"server {name!r}: key {key!r} must be an object of string -> string, "
            f"got {type(value).__name__}"
        )
    resolved: dict[str, str] = {}
    for entry, raw in value.items():
        if not isinstance(entry, str) or not isinstance(raw, str):
            raise ConfigError(
                f"server {name!r}: key {key!r} must map strings to strings; "
                f"{entry!r} maps to {type(raw).__name__}"
            )
        resolved[entry] = _expand(name, key, entry, raw, environ)
    return resolved


def _expand(server: str, key: str, entry: str, raw: str, environ: Mapping[str, str]) -> str:
    """Substitute ``${VAR}`` from the injected environment, or refuse.

    An unset variable becomes an error rather than an empty string: sending an
    empty bearer token to a remote server produces a 401 whose cause is three
    layers away from the typo that caused it.
    """
    out: list[str] = []
    rest = raw
    while True:
        start = rest.find("${")
        if start < 0:
            out.append(rest)
            return "".join(out)
        end = rest.find("}", start)
        if end < 0:
            raise ConfigError(
                f"server {server!r}: key {key!r}[{entry!r}] has an unterminated '${{' reference"
            )
        out.append(rest[:start])
        variable = rest[start + 2 : end]
        if variable not in environ:
            raise ConfigError(
                f"server {server!r}: key {key!r}[{entry!r}] references "
                f"${{{variable}}}, which is not set in the environment handed to "
                "the config loader"
            )
        out.append(environ[variable])
        rest = rest[end + 1 :]


__all__ = [
    "MCP_CONFIG_RELATIVE_PATH",
    "SERVERS_KEY",
    "UNDECLARED_DANGER_LEVEL",
    "AuthKind",
    "ConfigError",
    "McpServerConfig",
    "TransportKind",
    "enabled_servers",
    "load_mcp_config",
    "parse_mcp_config",
]
