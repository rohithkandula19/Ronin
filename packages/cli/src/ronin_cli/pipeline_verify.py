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


class SuiteRun(BaseModel):
    """Result of one named verification suite. Serializable."""
    name: str = ""
    command: str = ""
    required: bool = True
    status: str = "unknown"   # passed / failed / blocked / skipped / unknown
    exit_code: int | None = None
    duration: float = 0.0
    output_summary: str = ""
    declined: bool = False
    timed_out: bool = False
    verdict_effect: str = "none"  # failed / blocked / warning / none


def _verdict_effect(status: str, required: bool) -> str:
    """How a suite result affects the final verdict.

    Required failure → 'failed', required blocked → 'blocked'. An **optional**
    failure/blocked is only a 'warning' (never fails or blocks the run)."""
    if status == "failed":
        return "failed" if required else "warning"
    if status == "blocked":
        return "blocked" if required else "warning"
    return "none"


def parse_verify_suite(spec: str, *, default_required: bool = True) -> tuple[str, str, bool]:
    """Parse ``"name:command"`` → ``(name, command, required)``.

    A trailing ``?`` on the name marks the suite **optional** (e.g.
    ``"lint?:ruff check ."``). A spec with no colon becomes an unnamed
    ``verify`` suite. ``default_required`` sets the default when no ``?`` is given
    (so ``--optional-suite`` can flip it)."""
    spec = (spec or "").strip()
    name, sep, cmd = spec.partition(":")
    if not sep:
        return "verify", spec, default_required
    name = name.strip()
    required = default_required
    if name.endswith("?"):
        name, required = name[:-1].strip(), False
    return (name or "verify", cmd.strip(), required)


# suite name -> required? for --auto-verify-all classification
_SUITE_REQUIRED = {"tests": True, "unit": True, "build": True,
                   "typecheck": False, "lint": False, "format": False}


def auto_detect_suites(root) -> list[tuple[str, str, bool]]:
    """Best-effort detection of verification suites for ``--auto-verify-all``.

    Returns ``(name, command, required)`` — tests/unit/build are **required**;
    lint/typecheck/format are **optional** (a failing linter shouldn't fail the
    whole run). Conservative: only suggests a suite when its tool/config is
    plausibly present. Running each is still gated."""
    import shutil
    from pathlib import Path

    root = Path(root)
    raw: list[tuple[str, str]] = []

    tests = auto_detect_verify_command(root)
    if tests is not None:
        raw.append(("tests", tests[0]))

    py = (root / "pyproject.toml").is_file() or (root / "setup.cfg").is_file() \
        or any(root.glob("*.py"))
    uv = "uv run " if shutil.which("uv") else ""
    if py:
        pp = (root / "pyproject.toml")
        wants_ruff = shutil.which("ruff") or (pp.is_file() and "ruff" in pp.read_text(
            encoding="utf-8", errors="ignore"))
        if wants_ruff:
            raw.append(("lint", f"{uv}ruff check ."))
        if shutil.which("mypy") or (pp.is_file() and "mypy" in pp.read_text(
                encoding="utf-8", errors="ignore")):
            raw.append(("typecheck", f"{uv}mypy ."))

    if (root / "package.json").is_file():
        try:
            import json as _json
            scripts = (_json.loads((root / "package.json").read_text(encoding="utf-8"))
                       .get("scripts") or {})
        except (ValueError, OSError):
            scripts = {}
        pm = "pnpm" if (root / "pnpm-lock.yaml").is_file() else \
            ("yarn" if (root / "yarn.lock").is_file() else "npm")
        for suite, script in (("lint", "lint"), ("typecheck", "typecheck"), ("build", "build")):
            if script in scripts:
                raw.append((suite, f"{pm} run {script}"))
        if (root / "tsconfig.json").is_file() and "typecheck" not in dict(raw):
            raw.append(("typecheck", "npx tsc --noEmit"))

    seen, out = set(), []
    for name, cmd in raw:  # de-dup by command, keep first, classify required
        if cmd not in seen:
            seen.add(cmd)
            out.append((name, cmd, _SUITE_REQUIRED.get(name, True)))
    return out


def run_suite(name: str, command: str, root, *, required: bool = True, timeout: int = 600,
              yolo: bool = False, console=None, approve_fn=None, run_fn=None,
              clock=None) -> SuiteRun:
    """Run one suite (gated, via :func:`independent_verify`) into a :class:`SuiteRun`."""
    clock = clock or _monotonic
    t0 = clock()
    iv = independent_verify(command, root, timeout=timeout, yolo=yolo, console=console,
                            approve_fn=approve_fn, run_fn=run_fn)
    status = {"passed": "passed", "failed": "failed", "blocked": "blocked",
              "not_provided": "skipped"}.get(iv.verdict(), "unknown")
    return SuiteRun(name=name, command=command, required=required, status=status,
                    exit_code=iv.exit_code, duration=round(clock() - t0, 3),
                    output_summary=iv.output_summary, declined=iv.declined,
                    timed_out=iv.timed_out, verdict_effect=_verdict_effect(status, required))


def run_suites(suites: list[tuple], root, *, timeout: int = 600,
               yolo: bool = False, console=None, approve_fn=None, run_fn=None,
               clock=None) -> list[SuiteRun]:
    """Run each ``(name, command[, required])`` suite in order, gated. A declined
    suite halts the rest (don't keep prompting once the user said no)."""
    results: list[SuiteRun] = []
    for spec in suites:
        name, command = spec[0], spec[1]
        required = spec[2] if len(spec) > 2 else True
        if console is not None:
            tag = "" if required else " [dim](optional)[/dim]"
            console.print(f"  [#6b7089]suite[/#6b7089] [bold]{name}[/bold]{tag}: "
                          f"[cyan]{command}[/cyan]", highlight=False)
        sr = run_suite(name, command, root, required=required, timeout=timeout, yolo=yolo,
                       console=console, approve_fn=approve_fn, run_fn=run_fn, clock=clock)
        results.append(sr)
        if sr.declined:
            break  # the user declined — stop asking
    return results


def aggregate_verify(suites: list[SuiteRun]) -> VerifyRun:
    """Collapse the suite results into a VerifyRun for the final verdict.

    Only **required** suites drive the verdict: any required failure → failed;
    any required blocked/declined → blocked; else (a required suite passed) →
    passed. When there are no required suites, the run is advisory-only
    (``requested=False``) — optional suites never independently confirm the work
    nor fail it."""
    required = [s for s in suites if s.required]
    if not required:
        return VerifyRun(requested=False)
    run = VerifyRun(requested=True, command="; ".join(s.name for s in required))
    if any(s.status == "failed" for s in required):
        run.ran = True
        run.passed = False
        return run
    if any(s.declined or s.status == "blocked" for s in required):
        run.declined = True
        return run
    if any(s.status == "passed" for s in required):
        run.ran = True
        run.passed = True
        return run
    return VerifyRun(requested=False)  # all required skipped → nothing really ran


def has_optional_warning(suites: list[SuiteRun]) -> bool:
    """True if any optional suite warned (failed/blocked) — advisory, non-fatal."""
    return any(not s.required and s.verdict_effect == "warning" for s in suites)


def _monotonic() -> float:
    import time
    return time.monotonic()


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
