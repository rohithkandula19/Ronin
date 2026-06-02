"""Tests for background process management — real short-lived processes."""
from __future__ import annotations

import time

from ronin_cli.bg_processes import BackgroundManager, build_background_tools


def _wait_exit(mgr, pid, timeout=5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "exited" in mgr.status():
            return
        time.sleep(0.05)


def test_start_logs_and_exit() -> None:
    mgr = BackgroundManager()
    pid = mgr.start("echo hello-bg")
    assert pid == 1
    _wait_exit(mgr, pid)
    logs = mgr.logs(pid)
    assert "hello-bg" in logs
    assert f"#{pid}" in logs


def test_status_lists_processes() -> None:
    mgr = BackgroundManager()
    mgr.start("sleep 5")
    status = mgr.status()
    assert "#1" in status and "running" in status


def test_stop_kills_process() -> None:
    mgr = BackgroundManager()
    pid = mgr.start("sleep 30")
    assert "running" in mgr.status()
    msg = mgr.stop(pid)
    assert f"#{pid}" in msg
    assert mgr.status() == "no background processes running"


def test_stop_all_cleans_up() -> None:
    mgr = BackgroundManager()
    mgr.start("sleep 30")
    mgr.start("sleep 30")
    mgr.stop_all()
    assert mgr.status() == "no background processes running"


def test_logs_unknown_id() -> None:
    mgr = BackgroundManager()
    assert "no background process #99" in mgr.logs(99)


def test_build_background_tools_shape_and_sensitivity() -> None:
    from ronin_cli.code_tools import SENSITIVE_TOOLS
    tools = {t.name: t for t in build_background_tools(".")}
    assert set(tools) == {"run_background", "background_logs", "background_status", "stop_background"}
    assert "run_background" in SENSITIVE_TOOLS  # gated like run_command


def test_tools_drive_a_real_process(tmp_path) -> None:
    tools = {t.name: t for t in build_background_tools(tmp_path)}
    out = tools["run_background"].handler(command="echo from-tool")
    assert "started background process" in out
    # the status + logs tools see it
    time.sleep(0.3)
    assert "from-tool" in tools["background_logs"].handler(id=1)
    tools["stop_background"].handler(id=1)
