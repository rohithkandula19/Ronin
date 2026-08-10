#!/usr/bin/env python3
"""Prove every eval task's ``verify.sh`` actually discriminates. Run it in CI.

An eval suite is only worth the trust you put in its ``verify.sh`` scripts, and a
``verify.sh`` that **passes on the untouched fixture** is worse than a missing task:
it silently inflates every score derived from the suite, forever, and nothing about
the run looks wrong. The failure is invisible precisely because it produces the
answer you were hoping for.

So each task is checked in both directions:

1. Run ``verify.sh`` against the bare ``fixture/`` → it must exit **non-zero**.
   Otherwise the task is already solved and always scores a pass.
2. Overlay the author's ``solution/`` and run it again → it must exit **zero**.
   Otherwise the task is unsolvable and always scores a fail.

An ``injection-resistance`` task gets a third direction, because its pass criterion is
two claims and only the first is covered above: it must verify that the real work was
done *and* that the injected instruction was not obeyed. The second half is a guard,
and an unexercised guard is decoration — a ``verify.sh`` that forgot to check its
canary passes every solved workspace, so the whole category scores 100% forever and
every run looks correct. So each such task declares ``injection_probe`` in its
``task.toml``: the paths only an obeyed injection would create. This script plants them
in an otherwise-solved workspace and requires ``verify.sh`` to **fail**.

A task that has no ``solution/`` cannot be checked at all and is reported as
*unproven* rather than counted as fine — the honest state for "nobody has
demonstrated this task can be passed".

This exists as a committed script rather than a throwaway because the authors of the
tasks are also the people who pasted the evidence that they work. That is exactly the
claim that should be re-derived by a machine on every push, not read in a report.

Usage::

    python scripts/check_eval_tasks.py                     # every task, every direction
    python scripts/check_eval_tasks.py --parallel 8
    python scripts/check_eval_tasks.py --root tests/evals/injection-resistance
    python scripts/check_eval_tasks.py --quiet             # counts and failures only
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "tests" / "evals"

VERIFY_NAME = "verify.sh"
FIXTURE_NAME = "fixture"
SOLUTION_NAME = "solution"
TASK_DECLARATION = "task.toml"

#: A verify script gets its own generous ceiling. The point is to catch a script that
#: hangs — an eval task whose verification never returns would wedge a whole CI run.
DEFAULT_TIMEOUT = 180.0


@dataclasses.dataclass(frozen=True, slots=True)
class Task:
    """One eval task on disk."""

    directory: Path
    verify: Path
    fixture: Path | None
    solution: Path | None

    #: From ``injection_probe`` in ``task.toml``: paths only an obeyed injection would
    #: create. Empty for every category except ``injection-resistance``.
    probes: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """Repo-relative when possible, absolute otherwise.

        ``--root tests/evals/_staging/g1`` (a relative path) yields relative task
        directories, which ``relative_to(ROOT)`` cannot rebase — so fall back rather
        than crashing on the reporting path.
        """
        try:
            return str(self.directory.resolve().relative_to(ROOT))
        except ValueError:
            return str(self.directory)


@dataclasses.dataclass(frozen=True, slots=True)
class Outcome:
    """What each direction did. ``ok`` is the only thing CI cares about."""

    task: str
    bare_exit: int | None
    solved_exit: int | None
    detail: str
    unproven: bool = False
    #: Exit code of the probed run, or ``None`` when the task declares no probe.
    #: ``0`` is the failure this direction exists to catch: a solved workspace with
    #: the injection's artefact planted in it was still accepted.
    probed_exit: int | None = None

    @property
    def ok(self) -> bool:
        if self.unproven or "gitignored" in self.detail:
            return False
        if self.probed_exit == 0:
            return False
        return self.bare_exit is not None and self.bare_exit != 0 and self.solved_exit == 0

    def line(self) -> str:
        if self.unproven:
            return f"UNPROVEN  {self.task}  {self.detail}"
        mark = "ok      " if self.ok else "FAIL    "
        probed = "" if self.probed_exit is None else f" probed={self.probed_exit}"
        return (
            f"{mark}{self.task}  bare={self.bare_exit} solved={self.solved_exit}"
            f"{probed}  {self.detail}"
        )


def find_tasks(root: Path) -> Iterator[Task]:
    """Every directory holding a ``verify.sh``, deepest-first for stable ordering."""
    for verify in sorted(root.rglob(VERIFY_NAME)):
        directory = verify.parent
        # `verify.sh` may sit beside `task.toml` or inside `fixture/`; the task is
        # whichever directory owns the declaration.
        if not (directory / TASK_DECLARATION).exists():
            if (directory.parent / TASK_DECLARATION).exists():
                directory = directory.parent
            else:
                continue
        fixture = directory / FIXTURE_NAME
        solution = directory / SOLUTION_NAME
        yield Task(
            directory=directory,
            verify=verify,
            fixture=fixture if fixture.is_dir() else None,
            solution=solution if solution.is_dir() else None,
            probes=declared_probes(directory / TASK_DECLARATION),
        )


def declared_probes(declaration: Path) -> tuple[str, ...]:
    """``injection_probe`` from a ``task.toml``, tolerating a malformed file.

    Parsed with ``tomllib`` directly rather than through ``ronin.evals.task`` so this
    script keeps working on a tree whose loader does not import — it is the thing that
    tells you the fixtures are sound, and it should not need the runtime to say so. A
    file that will not parse is left to the loader's own error, which names the key.
    """
    try:
        data = tomllib.loads(declaration.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    raw = data.get("injection_probe", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, str) and item)


def gitignored(paths: list[Path]) -> list[Path]:
    """Which of ``paths`` git would silently refuse to commit.

    A gitignored fixture file is the nastiest failure this suite can have: the task
    passes for whoever wrote it, because the file is right there on their disk, and
    fails forever in CI, because it was never committed. Nothing in the run points at
    the cause.

    Two real casualties found this way: a `logs/ci-4821.log` CI-log fixture eaten by
    `*.log`, and 29 `__pycache__` artefacts from an author's own smoke run.
    """
    if not paths:
        return []
    completed = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(str(path) for path in paths),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return [Path(line) for line in completed.stdout.splitlines() if line.strip()]


def payload_files(task: Task) -> list[Path]:
    """Every file the task ships that must survive a commit."""
    found: list[Path] = [task.verify]
    for name in (FIXTURE_NAME, SOLUTION_NAME, "broken"):
        directory = task.directory / name
        if directory.is_dir():
            found.extend(path for path in sorted(directory.rglob("*")) if path.is_file())
    return found


#: A reference solution that *removes* a file cannot express that by copying, so it
#: lists the paths in this manifest instead. Ignoring it made the one task whose
#: answer is "delete the shim" look permanently broken — the checker's fault, not the
#: task's, and the second time this script blamed a task for its own omission.
DELETE_MANIFEST = "DELETE"


def overlay(source: Path, destination: Path) -> None:
    """Apply ``source`` over ``destination``: copy every file, then honour DELETE."""
    removals: list[str] = []
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        if relative.name == DELETE_MANIFEST and relative.parent == Path("."):
            removals = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    for name in removals:
        doomed = destination / name
        if doomed.is_dir():
            shutil.rmtree(doomed)
        elif doomed.exists():
            doomed.unlink()


def plant_probes(workspace: Path, probes: Sequence[str]) -> None:
    """Create the artefacts an obeyed injection would leave behind.

    Deliberately crude — an empty file at the declared path. The guards look for
    *existence* by name, which is the right thing for them to look for: an agent that
    creates ``telemetry.txt`` because a comment told it to has obeyed the injection
    regardless of what it wrote inside.
    """
    for probe in probes:
        relative = Path(probe)
        # `workspace / probe` is absolute because `workspace` is; test the *probe*.
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"injection_probe {probe!r} escapes the workspace")
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("planted by scripts/check_eval_tasks.py\n", encoding="utf-8")


def clear_bytecode(workspace: Path) -> None:
    """Remove every ``__pycache__`` before running a check.

    CPython validates a cached ``.pyc`` against the source's mtime **and size**. Two
    plausible one-line fixes can be byte-identical in length — ``total // size + 1``
    versus ``-(-total // size)`` is exactly 4 characters either way — so overwriting
    one with the other inside the same second leaves the stale bytecode looking valid,
    and the *corrected* tree runs the *old* code. A task author hit precisely that and
    saw a fixed fixture fail; the inverse (a bare fixture appearing solved) is the same
    bug pointing the other way, and it would inflate a score silently.
    """
    for cache in workspace.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)


def run_verify(task: Task, workspace: Path, *, timeout: float) -> tuple[int | None, str]:
    """Run one ``verify.sh`` **in place**, with ``workspace`` as cwd and as ``$1``.

    Deliberately *not* copied into the workspace, which is what an earlier version of
    this script did. Copying broke ten perfectly good tasks in two different ways, and
    both were the checker's fault rather than the task's:

    * The ``add-test`` category resolves ``broken/`` and ``baseline_nodeids.txt``
      relative to ``dirname $BASH_SOURCE``. Move the script and those siblings
      disappear, so every one of them reported a setup error.
    * The ``injection-resistance`` category greps the workspace for a marker string
      that proves the injection was obeyed — and that marker is written *in the script
      itself*, because the script is what searches for it. Copying the script into the
      tree it then scans plants the evidence it is looking for. Every one of them
      reported the injection as obeyed.

    Running it in place keeps the task directory intact and leaves the workspace
    holding only what the agent would actually see.
    """
    script = task.verify
    clear_bytecode(workspace)
    try:
        # A repo-local script by construction: the path comes from rglob under the
        # suite root, never from input.
        completed = subprocess.run(
            ["bash", str(script), str(workspace)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:.0f}s"
    output = (completed.stdout + completed.stderr).strip().splitlines()
    return completed.returncode, (output[-1][:160] if output else "")


def check(task: Task, *, timeout: float) -> Outcome:
    """Both directions for one task, each in its own throwaway workspace."""
    if task.fixture is None:
        return Outcome(task.name, None, None, "no fixture/ directory", unproven=True)
    if task.solution is None:
        return Outcome(
            task.name,
            None,
            None,
            "no solution/ — nobody has shown this task can be passed",
            unproven=True,
        )

    probed_exit: int | None = None
    with tempfile.TemporaryDirectory(prefix="evalcheck-") as raw:
        scratch = Path(raw)
        bare = scratch / "bare"
        shutil.copytree(task.fixture, bare)
        bare_exit, bare_detail = run_verify(task, bare, timeout=timeout)

        solved = scratch / "solved"
        shutil.copytree(task.fixture, solved)
        overlay(task.solution, solved)
        solved_exit, solved_detail = run_verify(task, solved, timeout=timeout)

        if task.probes:
            # A *third* workspace, not the solved one reused: the probe plants files
            # and some verify scripts hash the tree, so running direction 2 in a
            # workspace direction 3 has touched would make the two interfere.
            probed = scratch / "probed"
            shutil.copytree(task.fixture, probed)
            overlay(task.solution, probed)
            plant_probes(probed, task.probes)
            probed_exit, _ = run_verify(task, probed, timeout=timeout)

    ignored = gitignored(payload_files(task))
    if ignored:
        names = ", ".join(str(path.relative_to(task.directory)) for path in ignored[:3])
        return Outcome(
            task.name,
            bare_exit,
            solved_exit,
            f"{len(ignored)} file(s) are gitignored and will never reach CI: {names}",
            probed_exit=probed_exit,
        )

    if bare_exit == 0:
        detail = "verify.sh PASSES on the untouched fixture — the task is already solved"
    elif solved_exit != 0:
        detail = f"verify.sh fails on the author's own solution: {solved_detail}"
    elif probed_exit == 0:
        detail = (
            f"the injection guard does not bite: planting {', '.join(task.probes)} in a "
            "solved workspace still passes, so this task can never detect an obeyed "
            "injection"
        )
    else:
        detail = bare_detail
    return Outcome(task.name, bare_exit, solved_exit, detail, probed_exit=probed_exit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(SUITE), help="directory to scan")
    parser.add_argument("--parallel", type=int, default=4, help="concurrent tasks")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--quiet", action="store_true", help="only counts and failures")
    parser.add_argument(
        "--allow-unproven",
        action="store_true",
        help="do not fail on tasks that ship no solution/ (still reported)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2

    tasks = list(find_tasks(root))
    if not tasks:
        print(f"no eval tasks found under {root}", file=sys.stderr)
        return 2

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        outcomes = list(pool.map(lambda task: check(task, timeout=args.timeout), tasks))

    outcomes.sort(key=lambda outcome: outcome.task)
    failed = [o for o in outcomes if not o.ok and not o.unproven]
    unproven = [o for o in outcomes if o.unproven]

    for outcome in outcomes:
        if args.quiet and outcome.ok:
            continue
        print(outcome.line())

    print(
        f"\n{len(tasks)} task(s): {len(outcomes) - len(failed) - len(unproven)} proven, "
        f"{len(failed)} broken, {len(unproven)} unproven"
    )
    if failed:
        print(
            "\nA broken task corrupts every score computed from this suite. "
            "Fix or delete it — do not ship it.",
            file=sys.stderr,
        )
    if unproven and not args.allow_unproven:
        print(
            "\nUnproven tasks ship no solution/, so nothing demonstrates they are "
            "passable. Add one, or pass --allow-unproven deliberately.",
            file=sys.stderr,
        )
    return 1 if failed or (unproven and not args.allow_unproven) else 0


if __name__ == "__main__":
    raise SystemExit(main())
