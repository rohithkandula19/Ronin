"""Independent verification for the role pipeline.

When the user passes ``--verify-cmd``, the verifier doesn't just trust the
tester's claims — the pipeline harness *runs the command itself* and reconciles
the real exit code against what the tester reported. The command still goes
through an approval gate (a y/N) unless the user opted into auto-accept/yolo, and
a declined/timed-out/errored run is **blocked**, never **passed**.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from pydantic import BaseModel


class VerifyRun(BaseModel):
    """Result of an independent verification command. Serializable."""
    requested: bool = False
    command: str = ""
    ran: bool = False
    declined: bool = False
    timed_out: bool = False
    exit_code: int | None = None
    passed: bool | None = None
    output_summary: str = ""
    error: str = ""

    def verdict(self) -> str:
        """passed / failed / blocked / not_provided — never invents 'passed'."""
        if not self.requested:
            return "not_provided"
        if self.declined or self.timed_out or self.error:
            return "blocked"
        if not self.ran:
            return "blocked"
        return "passed" if self.passed else "failed"


def auto_detect_verify_command(root) -> tuple[str, str] | None:
    """Detect the repo's test/verification command via verify_cmd, as a shell
    string + runner label (e.g. ``("uv run pytest -q", "pytest")``), or None.

    Wraps the existing :func:`ronin_cli.verify_cmd.detect_verify_command`, which
    already covers uv/pytest, npm/pnpm/yarn, cargo, go, make, and a memory-file
    ``verify:`` line. Returns the command as a single string for the gated runner."""
    from .verify_cmd import detect_verify_command
    import shlex
    detected = detect_verify_command(root)
    if detected is None:
        return None
    cmd_list, runner = detected
    return shlex.join(cmd_list), runner


def _default_confirm(console, command: str) -> bool:
    """Existing-style shell approval prompt (default-deny)."""
    if console is not None:
        console.print(f"  [#7dcfff]$[/#7dcfff] [bold]{command}[/bold]")
        console.print("  [yellow]run this verification command?[/yellow] [grey50]y / N[/grey50] ", end="")
    try:
        return input().strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt, OSError):
        return False  # no interactive stdin → default-deny (never auto-run)


def independent_verify(
    command: str | None,
    root,
    *,
    timeout: int = 600,
    yolo: bool = False,
    console=None,
    approve_fn: Callable[[str], bool] | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess] | None = None,
) -> VerifyRun:
    """Run ``command`` (gated) and report a typed result. Never raises.

    - No command → ``requested=False`` (advisory mode preserved).
    - Not yolo and the gate declines → ``declined=True`` (verdict 'blocked').
    - Ran → ``passed`` from the exit code (0 = passed).

    ``approve_fn`` / ``run_fn`` are injectable for tests (no real shell needed)."""
    if not command or not command.strip():
        return VerifyRun(requested=False)

    run = VerifyRun(requested=True, command=command.strip())

    if not yolo:
        approve = approve_fn or (lambda cmd: _default_confirm(console, cmd))
        if not approve(run.command):
            run.declined = True
            return run

    runner = run_fn or _subprocess_run
    try:
        proc = runner(run.command, cwd=str(Path(root).resolve()), timeout=timeout)
    except subprocess.TimeoutExpired:
        run.timed_out = True
        run.error = f"timed out after {timeout}s"
        return run
    except (OSError, ValueError) as exc:
        run.error = str(exc)
        return run

    run.ran = True
    run.exit_code = proc.returncode
    run.passed = proc.returncode == 0
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    tail = out.splitlines()[-1] if out else ""
    run.output_summary = tail[:200]
    return run


def _subprocess_run(command: str, *, cwd: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(command, shell=True, cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)


def reconcile_with_tester(verify: VerifyRun, tester_verdict: str | None) -> str | None:
    """Note when the tester over-claimed vs the independent run, or None.

    Honesty signal for the final report: tester said passed but the real command
    failed/was blocked → flag it."""
    if not verify.requested:
        return None
    v = verify.verdict()
    if tester_verdict == "passed" and v == "failed":
        return "tester claimed passed but independent verification FAILED"
    if tester_verdict == "passed" and v == "blocked":
        return "tester claimed passed but independent verification could not run"
    return None
