"""Shared fakes and sample tool output for the verify tests.

Named ``verify_harness`` rather than ``harness``: the test trees are deliberately
not packages, so every module is importable by its bare basename and two
directories cannot share one — ``tests/tools/harness.py`` already exists.

The samples below are the real shape of the output the repair loop has to reason
about, including the parts that differ between two runs of the *same* failure. If
these ever get "cleaned up" the signature tests stop testing anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ronin.verify.runner import CommandFailure, CommandOutcome

# --------------------------------------------------------------------------- #
# A command runner that never spawns anything
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ScriptedRunner:
    """A :data:`~ronin.verify.runner.CommandRunner` driven by substring rules.

    Mutable because it records calls — that is the point of it. The first rule
    whose substring appears in the joined argv wins, so ``("pytest", 1, output)``
    fails every pytest invocation and leaves ruff alone.
    """

    rules: tuple[tuple[str, int, str], ...] = ()
    default_exit: int = 0
    default_output: str = ""
    failures: Mapping[str, CommandFailure] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    cwds: list[Path] = field(default_factory=list)

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del timeout, env
        recorded = tuple(str(part) for part in argv)
        self.calls.append(recorded)
        self.cwds.append(cwd)
        joined = " ".join(recorded)
        for needle, failure in self.failures.items():
            if needle in joined:
                return CommandOutcome(
                    argv=recorded, exit_code=127, output=f"{needle}: unavailable", failure=failure
                )
        for needle, exit_code, output in self.rules:
            if needle in joined:
                return CommandOutcome(argv=recorded, exit_code=exit_code, output=output)
        return CommandOutcome(
            argv=recorded, exit_code=self.default_exit, output=self.default_output
        )

    def argv_containing(self, needle: str) -> tuple[str, ...] | None:
        for call in self.calls:
            if needle in " ".join(call):
                return call
        return None


@dataclass(slots=True)
class QueueRunner:
    """Returns programmed outcomes in order, repeating the last one forever."""

    outcomes: tuple[CommandOutcome, ...]
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del cwd, timeout, env
        recorded = tuple(str(part) for part in argv)
        self.calls.append(recorded)
        index = min(len(self.calls) - 1, len(self.outcomes) - 1)
        return CommandOutcome(
            argv=recorded,
            exit_code=self.outcomes[index].exit_code,
            output=self.outcomes[index].output,
            failure=self.outcomes[index].failure,
        )


# --------------------------------------------------------------------------- #
# Sample project
# --------------------------------------------------------------------------- #

PYPROJECT_WITH_EVERYTHING = """\
[project]
name = "widget"
version = "0.1.0"

[dependency-groups]
dev = ["pytest>=8", "mypy>=1.11", "ruff>=0.6"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.mypy]
strict = true
"""


def make_python_project(root: Path, *, with_lockfile: bool = True) -> None:
    """A minimal repo that a detector should read exactly one way."""
    (root / "pyproject.toml").write_text(PYPROJECT_WITH_EVERYTHING, encoding="utf-8")
    if with_lockfile:
        (root / "uv.lock").write_text("# lock\n", encoding="utf-8")
    (root / ".gitignore").write_text(".ronin/\nsecret.txt\n", encoding="utf-8")
    (root / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "src" / "pkg" / "thing.py").write_text(
        "def widget(value: float) -> float:\n    return round(value, 2)\n", encoding="utf-8"
    )
    (root / "src" / "pkg" / "orphan.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "pkg" / "test_thing.py").write_text(
        "def test_widget_rounds() -> None:\n    assert True\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Sample tool output
# --------------------------------------------------------------------------- #

#: One failing test.
PYTEST_ONE_FAILURE = """\
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-8.3.2
rootdir: /tmp/pytest-of-alice/pytest-17/proj0
collected 3 items

tests/pkg/test_thing.py .F.                                              [100%]

=================================== FAILURES ===================================
_____________________________ test_widget_rounds ______________________________
tests/pkg/test_thing.py:14: in test_widget_rounds
    assert widget(1.005) == 1.01
E   AssertionError: assert 1.0 == 1.01
=========================== short test summary info ============================
FAILED tests/pkg/test_thing.py::test_widget_rounds - AssertionError: assert 1.0 == 1.01
========================= 1 failed, 2 passed in 0.11s ==========================
"""

#: The *same* failure on a later run. Everything that differs here is noise: a new
#: tmpdir, a line number that moved because an import was added above, a different
#: duration, an extra plugin line, and a heap address in a repr.
PYTEST_ONE_FAILURE_NOISY = """\
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-8.3.2
rootdir: /tmp/pytest-of-bob/pytest-9931/proj4
plugins: anyio-4.4.0, cov-5.0.0
collected 4 items

tests/pkg/test_thing.py .F..                                             [100%]

=================================== FAILURES ===================================
_____________________________ test_widget_rounds ______________________________
tests/pkg/test_thing.py:41: in test_widget_rounds
    assert widget(1.005) == 1.01
E   AssertionError: assert 1.0 == 1.01
=========================== short test summary info ============================
FAILED tests/pkg/test_thing.py::test_widget_rounds - AssertionError: assert 1.0 == 1.01
======================== 1 failed, 3 passed in 12.44 seconds ===================
"""

#: A genuinely different failure: a different test, a different assertion.
PYTEST_OTHER_FAILURE = """\
=================================== FAILURES ===================================
______________________________ test_widget_floors _____________________________
tests/pkg/test_thing.py:22: in test_widget_floors
    assert widget(2.5) == 2
E   AssertionError: assert 2.5 == 2
=========================== short test summary info ============================
FAILED tests/pkg/test_thing.py::test_widget_floors - AssertionError: assert 2.5 == 2
========================= 1 failed, 2 passed in 0.09s ==========================
"""

#: Two failing tests, for progress/regression comparisons.
PYTEST_TWO_FAILURES = """\
=================================== FAILURES ===================================
_____________________________ test_widget_rounds ______________________________
tests/pkg/test_thing.py:14: in test_widget_rounds
E   AssertionError: assert 1.0 == 1.01
______________________________ test_widget_floors _____________________________
tests/pkg/test_thing.py:22: in test_widget_floors
E   AssertionError: assert 2.5 == 2
=========================== short test summary info ============================
FAILED tests/pkg/test_thing.py::test_widget_rounds - AssertionError: assert 1.0 == 1.01
FAILED tests/pkg/test_thing.py::test_widget_floors - AssertionError: assert 2.5 == 2
========================= 2 failed, 1 passed in 0.13s ==========================
"""

MYPY_TWO_ERRORS = """\
src/thing.py:7: error: Incompatible return value type (got "int", expected "str")  [return-value]
src/thing.py:19: error: Argument 1 to "widget" has incompatible type "str"  [arg-type]
Found 2 errors in 1 file (checked 3 source files)
"""

#: The same two mypy errors after an unrelated edit shifted every line down.
MYPY_TWO_ERRORS_SHIFTED = """\
src/thing.py:23: error: Incompatible return value type (got "int", expected "str")  [return-value]
src/thing.py:35: error: Argument 1 to "widget" has incompatible type "str"  [arg-type]
Found 2 errors in 1 file (checked 4 source files)
"""

RUFF_ONE_ERROR = """\
src/pkg/thing.py:1:8: F401 [*] `os` imported but unused
Found 1 error.
"""


__all__ = [
    "MYPY_TWO_ERRORS",
    "MYPY_TWO_ERRORS_SHIFTED",
    "PYPROJECT_WITH_EVERYTHING",
    "PYTEST_ONE_FAILURE",
    "PYTEST_ONE_FAILURE_NOISY",
    "PYTEST_OTHER_FAILURE",
    "PYTEST_TWO_FAILURES",
    "RUFF_ONE_ERROR",
    "QueueRunner",
    "ScriptedRunner",
    "make_python_project",
]
