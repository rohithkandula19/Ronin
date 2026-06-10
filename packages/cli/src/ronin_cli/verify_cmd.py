"""Polyglot verify-command detection — the engine behind the red-to-green loop.

Ronin's autonomy stack (kaizen / dojo / nightshift) and the ``/verify`` REPL
pass used to hardcode ``uv run pytest``, so every Node / Go / Rust patch died
as "could not run tests". ``detect_verify_command`` finds the right command for
*this* repo, ``run_verify`` runs it, and ``parse_verdict`` turns per-runner
output into one neutral :class:`Verdict` (pass/fail + a short summary).

Detection order (first match wins):

1. An explicit ``verify:`` line in RONIN.md / CLAUDE.md / AGENTS.md — the user's
   own command always wins (e.g. ``verify: make check``).
2. ``package.json`` with a ``test`` script → the repo's package manager + test.
3. ``Cargo.toml`` → ``cargo test``.
4. ``go.mod`` → ``go test ./...``.
5. A ``Makefile`` with a ``test:`` target → ``make test``.
6. Python markers (pyproject ``[tool.pytest]`` / ``pytest.ini`` / a ``tests``
   dir / ``conftest.py``) → ``uv run pytest -q`` (or ``pytest -q``).

Everything here is offline and pure except ``run_verify`` (which shells out).
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .project_memory import MEMORY_FILENAMES


@dataclass
class Verdict:
    """Neutral result of a verify run, across any test runner."""

    passed: bool
    ran: bool          # False → no command could be detected/run (not a failure)
    command: str       # the command line that ran (or "" if none)
    runner: str        # "pytest" | "node" | "cargo" | "go" | "make" | "none"
    summary: str       # one-line human summary (the runner's own tail when useful)
    output: str = ""   # captured stdout+stderr tail, for feeding a repair turn


def _verify_from_memory(root: Path) -> tuple[list[str], str] | None:
    """An explicit ``verify: <command>`` line in a memory file beats all guessing."""
    for name in MEMORY_FILENAMES:
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            # allow an optional markdown bullet (`- verify: ...`)
            m = re.match(r"\s*[-*]?\s*verify\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
            if m and m.group(1).strip():
                try:
                    # shlex honors quotes (`pytest -k "not slow"`) and strips a
                    # trailing `# comment` — naive .split() mangled both.
                    parts = shlex.split(m.group(1).strip(), comments=True)
                except ValueError:
                    parts = m.group(1).strip().split()
                if parts:
                    return (parts, "memory")
    return None


def _node_test_command(root: Path) -> list[str] | None:
    pkg = root / "package.json"
    if not pkg.is_file():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    scripts = data.get("scripts") or {}
    test_script = str(scripts.get("test", "")).strip()
    if not test_script:
        return None
    # Skip placeholder / no-op scripts so they don't shadow a real test suite
    # (npm-init's default, or a bare `echo` stub like ronin's own root package).
    # A real test command runs a runner (jest/vitest/mocha/node --test/…), never
    # a lone echo, and never the npm "no test specified" placeholder.
    low = test_script.lower()
    if low.startswith("echo") or "no test specified" in low:
        return None
    # pick the package manager from the lockfile present
    if (root / "pnpm-lock.yaml").is_file():
        return ["pnpm", "test"]
    if (root / "yarn.lock").is_file():
        return ["yarn", "test"]
    if (root / "bun.lockb").is_file():
        return ["bun", "test"]
    return ["npm", "test"]


def _make_has_test_target(root: Path) -> bool:
    mk = root / "Makefile"
    if not mk.is_file():
        return False
    try:
        text = mk.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return re.search(r"^test\s*:", text, re.MULTILINE) is not None


def _has_python_tests(root: Path) -> bool:
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        return True
    if (root / "tests").is_dir() or (root / "test").is_dir():
        return True
    pp = root / "pyproject.toml"
    if pp.is_file():
        try:
            return "[tool.pytest" in pp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
    return False


def detect_verify_command(root: Path | str) -> tuple[list[str], str] | None:
    """Return ``(command, runner)`` for this repo, or ``None`` if none found."""
    root = Path(root)

    mem = _verify_from_memory(root)
    if mem is not None:
        return mem

    node = _node_test_command(root)
    if node is not None:
        return (node, "node")

    if (root / "Cargo.toml").is_file():
        return (["cargo", "test"], "cargo")

    if (root / "go.mod").is_file():
        return (["go", "test", "./..."], "go")

    if _make_has_test_target(root):
        return (["make", "test"], "make")

    if _has_python_tests(root):
        # prefer uv (works in a fresh worktree checkout); fall back to bare pytest
        if shutil.which("uv"):
            return (["uv", "run", "pytest", "-q"], "pytest")
        return (["pytest", "-q"], "pytest")

    return None


# ---- per-runner output parsing → (passed, summary) ----

_PYTEST_SUMMARY = re.compile(r"(\d+) (passed|failed|error[s]?)")


def _parse_pytest(output: str, returncode: int) -> tuple[bool, str]:
    counts = {kind: int(n) for n, kind in _PYTEST_SUMMARY.findall(output)}
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
    tail = [ln for ln in output.splitlines() if ln.strip()]
    summary = tail[-1][:200] if tail else "(no output)"
    ok = returncode == 0 and failed == 0 and (passed + failed) > 0
    return ok, summary


def _parse_cargo(output: str, returncode: int) -> tuple[bool, str]:
    # `cargo test` exits non-zero on any failing binary → the returncode is
    # authoritative. For the summary, prefer a FAILED line over the first 'ok'
    # one (a crate prints one `test result:` per binary, and re.search would
    # otherwise report an earlier binary's 'ok' on a failed run).
    results = re.findall(r"test result:.*", output)
    failed_line = next((r for r in results if "FAILED" in r), None)
    summary = (failed_line or (results[-1] if results else "(cargo test)"))[:200]
    return returncode == 0, summary


def _parse_go(output: str, returncode: int) -> tuple[bool, str]:
    # `go test ./...` exits non-zero on failure → trust the returncode (a bare
    # `"FAIL" in output` substring would false-fail on e.g. an uppercase import
    # path). Surface a real FAIL line in the summary when present.
    fail_line = next((ln for ln in output.splitlines()
                      if ln.startswith(("FAIL", "--- FAIL"))), None)
    last = [ln for ln in output.splitlines() if ln.strip()]
    summary = (fail_line or (last[-1] if last else "(go test)"))[:200]
    return returncode == 0, summary


def _parse_generic(output: str, returncode: int) -> tuple[bool, str]:
    """Node/jest/make/etc. — trust the exit code, summarize the tail."""
    last = [ln for ln in output.splitlines() if ln.strip()]
    summary = (last[-1][:200] if last else "(no output)")
    return returncode == 0, summary


_PARSERS = {"pytest": _parse_pytest, "cargo": _parse_cargo, "go": _parse_go}


def parse_verdict(runner: str, output: str, returncode: int) -> tuple[bool, str]:
    return _PARSERS.get(runner, _parse_generic)(output, returncode)


def run_verify(root: Path | str, *, timeout: int = 600) -> Verdict:
    """Detect and run this repo's verify command; return a neutral Verdict.

    ``ran=False`` means no test command could be detected — distinct from a real
    failure, so callers don't treat an untested repo as broken."""
    root = Path(root).resolve()
    detected = detect_verify_command(root)
    if detected is None:
        return Verdict(passed=False, ran=False, command="", runner="none",
                       summary="no test command detected for this repo")
    cmd, runner = detected
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True,
                              text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return Verdict(passed=False, ran=False, command=" ".join(cmd), runner=runner,
                       summary=f"could not run `{' '.join(cmd)}`: {e}")
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    passed, summary = parse_verdict(runner, output, proc.returncode)
    # keep a bounded tail for feeding into a repair turn
    tail = "\n".join(output.splitlines()[-60:])
    return Verdict(passed=passed, ran=True, command=" ".join(cmd), runner=runner,
                   summary=summary, output=tail)
