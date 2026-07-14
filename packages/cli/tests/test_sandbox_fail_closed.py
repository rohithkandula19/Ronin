"""RONIN_BACKEND requests containment; a failure to honor it must FAIL CLOSED.

Before this, `run_command` did `except Exception: pass` and ran on the host when
the backend errored (Docker not installed, container gone, timeout). So a user who
set RONIN_BACKEND to contain the agent had destructive commands silently execute on
their real machine. Phase A: "never silently execute outside the requested sandbox."

The decisive tests set a real side-effecting command and assert the side effect
NEVER happens on the host when the backend fails.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli import backends
from ronin_cli.code_tools import build_code_tools


def _run_command_tool(root: Path):
    return next(t for t in build_code_tools(root) if t.name == "run_command")


def _run_background_tool(root: Path):
    from ronin_cli.bg_processes import build_background_tools
    return next(t for t in build_background_tools(root) if t.name == "run_background")


# ---------- requested_backend: what counts as "contain me" ----------

@pytest.mark.parametrize("val,expected", [
    (None, None), ("", None), ("local", None), ("LOCAL", None), ("  local ", None),
    ("docker:box", "docker:box"), ("ssh:user@host", "ssh:user@host"),
])
def test_requested_backend(val, expected, monkeypatch):
    if val is None:
        monkeypatch.delenv("RONIN_BACKEND", raising=False)
    else:
        monkeypatch.setenv("RONIN_BACKEND", val)
    assert backends.requested_backend() == expected


# ---------- the fail-open bug, now closed ----------

def test_backend_failure_does_not_run_on_host(tmp_path, monkeypatch):
    marker = tmp_path / "RAN_ON_HOST"
    monkeypatch.setenv("RONIN_BACKEND", "docker:does-not-exist")
    # simulate the backend being unavailable (Docker missing / daemon down / timeout)
    def _boom(*a, **k):
        raise FileNotFoundError("docker: command not found")
    monkeypatch.setattr(backends, "run_in_backend", _boom)

    tool = _run_command_tool(tmp_path)
    out = tool.handler(command=f"touch {marker}")
    assert "blocked" in out.lower() and "sandbox" in out.lower()
    assert not marker.exists(), "a command the user asked to CONTAIN ran on the host"


def test_invalid_backend_spec_is_refused_not_run_locally(tmp_path, monkeypatch):
    marker = tmp_path / "RAN_ON_HOST"
    monkeypatch.setenv("RONIN_BACKEND", "nonsense-not-a-backend")
    tool = _run_command_tool(tmp_path)
    out = tool.handler(command=f"touch {marker}")
    assert "blocked" in out.lower()
    assert not marker.exists()


def test_no_backend_runs_on_host_as_normal(tmp_path, monkeypatch):
    monkeypatch.delenv("RONIN_BACKEND", raising=False)
    marker = tmp_path / "ok"
    tool = _run_command_tool(tmp_path)
    out = tool.handler(command=f"touch {marker}")
    assert "exit=0" in out
    assert marker.exists(), "with no sandbox requested, host execution is expected"


def test_local_backend_runs_on_host(tmp_path, monkeypatch):
    monkeypatch.setenv("RONIN_BACKEND", "local")   # explicit host-on-purpose
    marker = tmp_path / "ok"
    _run_command_tool(tmp_path).handler(command=f"touch {marker}")
    assert marker.exists()


def test_successful_backend_result_is_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("RONIN_BACKEND", "docker:box")
    monkeypatch.setattr(backends, "run_in_backend",
                        lambda *a, **k: (0, "hello from sandbox", ""))
    out = _run_command_tool(tmp_path).handler(command="echo hi")
    assert "exit=0" in out and "hello from sandbox" in out


# ---------- run_background also fails closed under a requested sandbox ----------

def test_run_background_refuses_under_requested_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("RONIN_BACKEND", "docker:box")
    out = _run_background_tool(tmp_path).handler(command="sleep 999")
    assert "blocked" in out.lower()
    # and it did NOT start anything (no process id handed back)
    assert "background process #" not in out


def test_run_background_works_without_sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv("RONIN_BACKEND", raising=False)
    out = _run_background_tool(tmp_path).handler(command="true")
    assert "background process #" in out
