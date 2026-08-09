#!/usr/bin/env bash
# Workspace root: first argument if given, otherwise the current directory.
set -u
ROOT="${1:-$PWD}"
PY="${PYTHON:-python3}"
cd "$ROOT" || { echo "verify: workspace root '$ROOT' does not exist"; exit 2; }

"$PY" - <<'PYCODE'
import ast
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "src")

WORK = Path(".verify-tmp")


def fail(message: str) -> None:
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"verify: {message}")
    raise SystemExit(1)


try:
    from hooks.render import HOOK_TEMPLATE, render_hook, run_checks
except Exception as exc:  # noqa: BLE001
    fail(f"could not import hooks.render: {exc!r}")

OK = [sys.executable, "-c", ""]
BAD = [sys.executable, "-c", "raise SystemExit(3)"]


def hook_for(checks: list[tuple[str, list[str]]]) -> Path:
    try:
        source = render_hook(checks)
    except Exception as exc:  # noqa: BLE001
        fail(
            "render_hook() raised after the edit - the template is filled in with "
            f"str.format, so literal braces in it must stay doubled: {exc!r}"
        )
    try:
        ast.parse(source)
    except SyntaxError as exc:
        fail(f"the rendered hook is not valid python: {exc}")
    WORK.mkdir(exist_ok=True)
    path = WORK / "pre-commit"
    path.write_text(source, encoding="utf-8")
    return path


# 1. A failing check must stop the commit.
path = hook_for([("lint", BAD), ("types", OK)])
done = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
if done.returncode == 0:
    fail(
        "expected the generated hook to exit non-zero when a check fails, got "
        f"exit 0 (stderr: {done.stderr.strip()!r})"
    )
if "hook: lint failed" not in done.stderr:
    fail(
        "expected the generated hook to still report 'hook: lint failed' on stderr, "
        f"got stderr {done.stderr.strip()!r}"
    )

# 2. Every check must still run, so one commit shows every problem.
path = hook_for([("lint", BAD), ("types", BAD), ("unit", BAD)])
done = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
if done.returncode == 0:
    fail("expected the generated hook to exit non-zero with three failing checks, got exit 0")
missing = [n for n in ("lint", "types", "unit") if f"hook: {n} failed" not in done.stderr]
if missing:
    fail(
        "the generated hook stops at the first failure: expected all of lint, types, unit "
        f"to be reported, missing {missing} (stderr: {done.stderr.strip()!r})"
    )

# 3. A clean run must still succeed silently.
path = hook_for([("lint", OK), ("types", OK)])
done = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
if done.returncode != 0:
    fail(
        "expected the generated hook to exit 0 when every check passes, got "
        f"exit {done.returncode} (stderr: {done.stderr.strip()!r})"
    )
if "failed" in done.stderr:
    fail(f"expected no failure output on a clean run, got stderr {done.stderr.strip()!r}")

# 4. The generated file is still a hook git can execute, and still says where it came from.
lines = HOOK_TEMPLATE.splitlines()
first = lines[0] if lines else ""
if first != "#!/usr/bin/env python3":
    fail(f"the template lost its shebang: its first line is {first!r}")
if "do not edit by hand" not in HOOK_TEMPLATE:
    fail("the template lost its 'do not edit by hand' banner")

# 5. The in-process checker is a different code path and must be left alone.
if run_checks([("lint", OK)]) != 0:
    fail("expected run_checks() to return 0 when every check passes")
if run_checks([("lint", BAD)]) != 1:
    fail("expected run_checks() to return 1 when a check fails")

shutil.rmtree(WORK, ignore_errors=True)
PYCODE
