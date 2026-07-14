"""MCP servers must not inherit the whole environment.

Before this, every MCP server was spawned with ``env={**os.environ, ...}``, so an
arbitrary ``npx -y <third-party-package>`` from the catalog received ANTHROPIC_API_KEY,
AWS creds, GITHUB_TOKEN — every secret in the user's shell. Now a server gets only
operational vars plus the parent vars it explicitly DECLARED.

The decisive tests actually SPAWN a subprocess with the scoped env and read what it
saw — proving the secret never crosses the process boundary, not merely that a dict
looks right.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from ronin_cli import mcp_client
from ronin_cli.mcp_client import (
    MCPClient,
    _base_env,
    scoped_env,
    split_env_spec,
)


@pytest.fixture
def fake_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET-must-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AWS-SECRET-must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-declared-and-allowed")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-must-not-leak")


def test_base_env_has_operational_vars_and_no_secrets(fake_secrets):
    base = _base_env()
    assert base.get("PATH") == "/usr/bin:/bin"
    assert base.get("HOME") == "/home/tester"
    for secret in ("ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY"):
        assert secret not in base, f"{secret} leaked into base env"


def test_scoped_env_passes_only_declared_secret(fake_secrets):
    env = scoped_env(pass_env=["GITHUB_TOKEN"], explicit=None)
    assert env["GITHUB_TOKEN"] == "ghp-declared-and-allowed"   # declared → allowed
    assert env["PATH"] == "/usr/bin:/bin"                      # operational → allowed
    assert "ANTHROPIC_API_KEY" not in env                     # undeclared → withheld
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "OPENAI_API_KEY" not in env


def test_explicit_env_wins_and_unset_passthrough_is_omitted(fake_secrets):
    env = scoped_env(pass_env=["NOT_SET_ANYWHERE", "GITHUB_TOKEN"],
                     explicit={"GITHUB_TOKEN": "literal-override"})
    assert env["GITHUB_TOKEN"] == "literal-override"    # explicit KEY=VALUE wins
    assert "NOT_SET_ANYWHERE" not in env                # declared but unset → omitted


def test_split_env_spec():
    env_map, pass_env = split_env_spec(["FOO=bar", "GITHUB_TOKEN", "BAZ=qux"])
    assert env_map == {"FOO": "bar", "BAZ": "qux"}
    assert pass_env == ["GITHUB_TOKEN"]


# ---------- the real proof: a genuinely spawned subprocess ----------

_DUMP_ENV = "import os, json, sys; sys.stdout.write(json.dumps(dict(os.environ)))"


def _spawn_and_read_env(client: MCPClient) -> dict:
    """Spawn `python -c <dump env>` through the SAME env path MCPClient.start uses,
    and read back exactly what the child process received."""
    proc = subprocess.run(
        [sys.executable, "-c", _DUMP_ENV],
        capture_output=True, text=True,
        env=scoped_env(client.pass_env, client.env),
    )
    return json.loads(proc.stdout or "{}")


def test_spawned_server_cannot_see_undeclared_secrets(fake_secrets):
    client = MCPClient("evil", sys.executable, pass_env=[])   # declares nothing
    child_env = _spawn_and_read_env(client)
    assert "ANTHROPIC_API_KEY" not in child_env, "a spawned MCP server saw ANTHROPIC_API_KEY"
    assert "AWS_SECRET_ACCESS_KEY" not in child_env
    assert "GITHUB_TOKEN" not in child_env
    assert child_env.get("PATH"), "operational PATH must still be present"


def test_spawned_server_sees_its_declared_secret_only(fake_secrets):
    client = MCPClient("github", sys.executable, pass_env=["GITHUB_TOKEN"])
    child_env = _spawn_and_read_env(client)
    assert child_env.get("GITHUB_TOKEN") == "ghp-declared-and-allowed"
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "OPENAI_API_KEY" not in child_env


def test_catalog_migration_gives_old_config_its_declared_names(monkeypatch):
    # An mcp.json entry named after a catalog server, with NO passEnv, still gets
    # the catalog's declared env names — so pre-passEnv configs keep working
    # without the old blanket leak.
    from ronin_cli.mcp_client import _catalog_pass_env
    names = _catalog_pass_env("github")
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in names
    assert _catalog_pass_env("definitely-not-a-catalog-server") == []
