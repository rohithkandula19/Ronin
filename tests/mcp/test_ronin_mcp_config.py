"""``.ronin/mcp.json``: strict validation, and the fail-closed trust defaults.

Every error case asserts that the message names the offending *server* and *key*,
because a config error whose message says only "invalid" leaves the operator
diffing JSON by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ronin.core.types import DangerLevel
from ronin.mcp.config import (
    MCP_CONFIG_RELATIVE_PATH,
    UNDECLARED_DANGER_LEVEL,
    ConfigError,
    McpServerConfig,
    TransportKind,
    enabled_servers,
    load_mcp_config,
    parse_mcp_config,
)


def write_config(root: Path, body: Any) -> Path:
    path = root / MCP_CONFIG_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


def test_a_stdio_server_parses_command_args_env_and_cwd() -> None:
    (config,) = parse_mcp_config(
        {
            "mcpServers": {
                "docs": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@scope/server"],
                    "env": {"TOKEN": "abc"},
                    "cwd": "/tmp/x",
                }
            }
        }
    )
    assert config.name == "docs"
    assert config.transport is TransportKind.STDIO
    assert config.argv == ("npx", "-y", "@scope/server")
    assert config.env == {"TOKEN": "abc"}
    assert config.cwd == "/tmp/x"


def test_transport_is_inferred_only_when_a_command_makes_it_unambiguous() -> None:
    (config,) = parse_mcp_config({"mcpServers": {"docs": {"command": "srv"}}})
    assert config.transport is TransportKind.STDIO


def test_a_url_without_a_declared_transport_is_an_error_naming_the_key() -> None:
    """A url alone does not say whether the server speaks sse or http."""
    with pytest.raises(ConfigError) as caught:
        parse_mcp_config({"mcpServers": {"remote": {"url": "https://x.invalid"}}})
    assert "remote" in str(caught.value)
    assert "'transport'" in str(caught.value)


@pytest.mark.parametrize("kind", ["sse", "http"])
def test_a_network_server_parses_url_and_headers(kind: str) -> None:
    (config,) = parse_mcp_config(
        {
            "mcpServers": {
                "remote": {
                    "transport": kind,
                    "url": "https://x.invalid/mcp",
                    "headers": {"authorization": "Bearer ${TOK}"},
                }
            }
        },
        environ={"TOK": "s3cret"},
    )
    assert config.transport is TransportKind(kind)
    assert config.headers == {"authorization": "Bearer s3cret"}


def test_a_missing_config_file_means_no_servers(tmp_path: Path) -> None:
    """Most repos have none; that is not a failure."""
    assert load_mcp_config(tmp_path) == ()


def test_a_config_file_is_read_from_the_documented_path(tmp_path: Path) -> None:
    write_config(tmp_path, {"mcpServers": {"docs": {"command": "srv"}}})
    (config,) = load_mcp_config(tmp_path)
    assert config.command == "srv"


def test_malformed_json_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / MCP_CONFIG_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_mcp_config(tmp_path)


def test_disabled_servers_are_parsed_but_filtered() -> None:
    configs = parse_mcp_config(
        {
            "mcpServers": {
                "a": {"command": "a"},
                "b": {"command": "b", "enabled": False},
            }
        }
    )
    assert [c.name for c in configs] == ["a", "b"]
    assert [c.name for c in enabled_servers(configs)] == ["a"]


# --------------------------------------------------------------------------- #
# Fail-closed danger defaults
# --------------------------------------------------------------------------- #


def test_a_server_that_declares_nothing_is_gated() -> None:
    """The rule: undeclared means unknown, unknown means approval."""
    (config,) = parse_mcp_config({"mcpServers": {"x": {"command": "x"}}})
    assert config.danger_level is None
    assert config.requires_approval is None
    assert config.effective_danger_level is UNDECLARED_DANGER_LEVEL
    assert config.effective_danger_level is DangerLevel.MUTATING
    assert config.effective_requires_approval is True


def test_waiving_the_gate_takes_both_keys() -> None:
    (config,) = parse_mcp_config(
        {
            "mcpServers": {
                "x": {"command": "x", "danger_level": "read_only", "requires_approval": False}
            }
        }
    )
    assert config.effective_danger_level is DangerLevel.READ_ONLY
    assert config.effective_requires_approval is False


def test_requires_approval_false_without_a_danger_level_is_refused() -> None:
    """The operator was reasoning about a danger they had not stated."""
    with pytest.raises(ConfigError) as caught:
        parse_mcp_config({"mcpServers": {"x": {"command": "x", "requires_approval": False}}})
    assert "x" in str(caught.value)
    assert "requires_approval" in str(caught.value)


@pytest.mark.parametrize("level", ["destructive", "irreversible"])
def test_a_destructive_server_cannot_opt_out_of_the_gate(level: str) -> None:
    (config,) = parse_mcp_config(
        {"mcpServers": {"x": {"command": "x", "danger_level": level, "requires_approval": False}}}
    )
    assert config.effective_requires_approval is True


def test_a_declared_read_only_server_still_defaults_to_gated() -> None:
    """Omitting requires_approval is not the same as setting it to false."""
    (config,) = parse_mcp_config(
        {"mcpServers": {"x": {"command": "x", "danger_level": "read_only"}}}
    )
    assert config.effective_requires_approval is True


# --------------------------------------------------------------------------- #
# Rejections
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ({"transport": "stdio"}, "'command'"),
        ({"transport": "http"}, "'url'"),
        ({"transport": "carrier-pigeon", "url": "x"}, "'transport'"),
        ({"transport": 3, "url": "x"}, "'transport'"),
        ({"command": "x", "url": "y"}, "'url'"),
        ({"transport": "http", "url": "x", "command": "y"}, "'command'"),
        ({"command": "x", "nonsense": 1}, "'nonsense'"),
        ({"command": "x", "args": "not-a-list"}, "'args'"),
        ({"command": "x", "args": [1]}, "'args'[0]"),
        ({"command": "x", "env": "nope"}, "'env'"),
        ({"command": "x", "env": {"A": 1}}, "'env'"),
        ({"command": "x", "danger_level": "spicy"}, "'danger_level'"),
        ({"command": "x", "danger_level": 2}, "'danger_level'"),
        ({"command": "x", "enabled": "yes"}, "'enabled'"),
        ({"command": "x", "timeout_seconds": 0}, "'timeout_seconds'"),
        ({"command": "x", "timeout_seconds": -1}, "'timeout_seconds'"),
        ({"command": "x", "timeout_seconds": "30"}, "'timeout_seconds'"),
        ({"command": ""}, "'command'"),
        ({"command": "x", "cwd": 7}, "'cwd'"),
    ],
)
def test_every_rejection_names_the_server_and_the_key(body: dict[str, Any], needle: str) -> None:
    with pytest.raises(ConfigError) as caught:
        parse_mcp_config({"mcpServers": {"docs": body}})
    message = str(caught.value)
    assert "docs" in message, message
    assert needle in message, message


def test_a_server_name_containing_the_separator_is_refused() -> None:
    """`a__b` would make mcp__a__b__tool ambiguous — the collision by construction."""
    with pytest.raises(ConfigError, match="__"):
        parse_mcp_config({"mcpServers": {"a__b": {"command": "x"}}})


def test_an_unknown_top_level_key_is_refused() -> None:
    with pytest.raises(ConfigError, match="mcpServers"):
        parse_mcp_config({"servers": {}})


def test_a_non_object_server_body_is_refused() -> None:
    with pytest.raises(ConfigError, match="must be an object"):
        parse_mcp_config({"mcpServers": {"docs": ["npx"]}})


def test_a_non_object_servers_map_is_refused() -> None:
    with pytest.raises(ConfigError, match="name -> server config"):
        parse_mcp_config({"mcpServers": []})


def test_an_unset_environment_reference_is_refused_rather_than_blanked() -> None:
    """An empty bearer token produces a 401 three layers from the typo."""
    with pytest.raises(ConfigError) as caught:
        parse_mcp_config(
            {"mcpServers": {"r": {"transport": "http", "url": "u", "headers": {"a": "${NOPE}"}}}},
            environ={},
        )
    assert "${NOPE}" in str(caught.value)
    assert "'headers'" in str(caught.value)


def test_an_unterminated_environment_reference_is_refused() -> None:
    with pytest.raises(ConfigError, match="unterminated"):
        parse_mcp_config(
            {"mcpServers": {"r": {"command": "x", "env": {"A": "${OPEN"}}}}, environ={}
        )


def test_expansion_handles_several_references_and_literal_text() -> None:
    (config,) = parse_mcp_config(
        {"mcpServers": {"r": {"command": "x", "env": {"A": "p-${U}-q-${V}"}}}},
        environ={"U": "1", "V": "2"},
    )
    assert config.env == {"A": "p-1-q-2"}


def test_the_dataclass_itself_refuses_an_incoherent_config() -> None:
    """The parser is not the only door; a hand-built config is checked too."""
    with pytest.raises(ValueError, match="needs a command"):
        McpServerConfig(name="x", transport=TransportKind.STDIO)
    with pytest.raises(ValueError, match="needs a url"):
        McpServerConfig(name="x", transport=TransportKind.SSE)
    with pytest.raises(ValueError, match="timeout_seconds"):
        McpServerConfig(name="x", transport=TransportKind.STDIO, command="c", timeout_seconds=0)
