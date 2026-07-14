"""macOS Seatbelt OS-native sandbox backend (Phase B containment).

The unit tests (profile generation, spec parsing, argv shape) run everywhere. The
DECISIVE tests actually invoke ``sandbox-exec`` and prove real containment — a
command cannot write outside the workspace — so they run only on macOS with
sandbox-exec present, and are honestly SKIPPED elsewhere (e.g. the Linux CI). A
skipped containment test is never reported as a pass.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from ronin_cli.backends import (
    parse_backend,
    run_in_backend,
    seatbelt_profile,
    wrap_command,
)

_ON_MAC = platform.system() == "Darwin" and shutil.which("sandbox-exec") is not None
_needs_seatbelt = pytest.mark.skipif(
    not _ON_MAC, reason="Seatbelt / sandbox-exec is macOS-only (SKIPPED, not passed)"
)


# ---------- unit: parsing + profile generation (run everywhere) ----------

def test_parse_seatbelt_specs():
    assert parse_backend("seatbelt") == {"type": "seatbelt", "allow_network": True}
    assert parse_backend("seatbelt:no-network") == {"type": "seatbelt", "allow_network": False}
    assert parse_backend("seatbelt:strict") == {"type": "seatbelt", "allow_network": False}


def test_profile_confines_writes_to_workspace(tmp_path):
    prof = seatbelt_profile(str(tmp_path))
    assert "(deny default)" in prof
    assert f'(subpath "{tmp_path}")' in prof            # workspace is write-allowed
    assert "(allow network*)" in prof                  # default allows network


def test_profile_can_deny_network():
    assert "(deny network*)" in seatbelt_profile("/x", allow_network=False)
    assert "(allow network*)" in seatbelt_profile("/x", allow_network=True)


def test_wrap_command_builds_sandbox_exec_argv(tmp_path, monkeypatch):
    # The argv shape is pure logic — fake macOS so it is testable on any OS
    # (Linux CI included), without needing a real sandbox-exec.
    monkeypatch.setattr("ronin_cli.backends.platform.system", lambda: "Darwin")
    argv = wrap_command("echo hi", {"type": "seatbelt", "workspace": str(tmp_path)})
    assert argv[0] == "sandbox-exec" and argv[1] == "-p"
    assert argv[-3:] == ["/bin/sh", "-c", "echo hi"]    # command carried as ONE element
    assert "(deny default)" in argv[2]


def test_non_macos_seatbelt_raises(monkeypatch):
    # On a non-mac host, wrap_command must refuse (→ the fail-closed selection
    # turns this into a refusal, never a silent host run).
    monkeypatch.setattr("ronin_cli.backends.platform.system", lambda: "Linux")
    with pytest.raises(ValueError, match="requires macOS"):
        wrap_command("echo hi", {"type": "seatbelt", "workspace": "/x"})


# ---------- integration: real containment (macOS only) ----------

@_needs_seatbelt
def test_write_inside_workspace_is_allowed(tmp_path):
    code, out, err = run_in_backend("touch inside.txt && echo done",
                                    {"type": "seatbelt"}, cwd=str(tmp_path))
    assert code == 0, err
    assert (tmp_path / "inside.txt").exists()


@_needs_seatbelt
def test_write_to_home_is_blocked(tmp_path):
    marker = Path.home() / "RONIN_SEATBELT_ESCAPE_TEST"
    marker.unlink(missing_ok=True)
    try:
        code, out, err = run_in_backend(f"touch '{marker}'",
                                        {"type": "seatbelt"}, cwd=str(tmp_path))
        assert not marker.exists(), "a sandboxed command wrote to $HOME — containment failed"
        assert code != 0                                # the write was denied
    finally:
        marker.unlink(missing_ok=True)


@_needs_seatbelt
def test_cannot_touch_another_project(tmp_path):
    # A real "other project" lives under $HOME, OUTSIDE the workspace and the temp
    # write-zone. (Placing it in /tmp would be writable by design — temp is
    # ephemeral — which is what the earlier version of this test tripped over.)
    victim = Path.home() / ".ronin_seatbelt_victim_test"
    victim.mkdir(exist_ok=True)
    keep = victim / "keep.txt"
    keep.write_text("precious")
    try:
        run_in_backend(f"rm -f '{keep}'", {"type": "seatbelt"}, cwd=str(tmp_path))
        assert keep.exists(), "sandbox let a command delete another project's file under $HOME"
    finally:
        shutil.rmtree(victim, ignore_errors=True)


@_needs_seatbelt
def test_reads_and_process_exec_still_work(tmp_path):
    # a real sandbox must not break ordinary commands
    code, out, err = run_in_backend("python3 -c 'print(6*7)' && ls / >/dev/null && echo ok",
                                    {"type": "seatbelt"}, cwd=str(tmp_path))
    assert code == 0, err
    assert "42" in out and "ok" in out


@_needs_seatbelt
def test_no_network_mode_blocks_the_net(tmp_path):
    # deny-network profile: a network op should fail; a local op should still work
    ok_code, _, _ = run_in_backend("echo local-ok",
                                   {"type": "seatbelt", "allow_network": False}, cwd=str(tmp_path))
    assert ok_code == 0
    net_code, _, _ = run_in_backend(
        "python3 -c 'import socket; socket.create_connection((\"1.1.1.1\",80),2)'",
        {"type": "seatbelt", "allow_network": False}, cwd=str(tmp_path))
    assert net_code != 0, "network was reachable under a no-network sandbox"
