"""The tool base class: the error boundary, the truncation net, the arg coercions.

Two standing rules of this project live in ``ronin.tools.base`` and had **no test**
before this file:

* *errors are values, not exceptions, across tool boundaries* — `Tool.execute`
* *every tool result the model sees is truncated deterministically, with a marker
  showing what was cut* — `Tool.execute` + `_truncate`

`test_files.py` covers ``read``'s line cap and `test_net.py` covers page truncation, but
both are tool-specific. The universal net in `execute` — the one that guarantees the
property for a tool nobody has written yet, including an MCP tool from a third party —
was never executed by the suite. A rule that is implemented but unverified is a rule that
regresses silently, which is the worst of the three states it could be in.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest
from harness import context

from ronin.core.types import DangerLevel, ToolResult
from ronin.tools.base import (
    MAX_RESULT_CHARS,
    Example,
    Tool,
    ToolContext,
    ToolError,
    optional_bool,
    optional_int,
    require_str,
)


class _Ok(Tool):
    """A minimal well-formed tool. Its `run` returns whatever it was handed."""

    name = "echo"
    summary = "Echo the content back."
    when_to_use = "Never; this exists for the boundary tests."
    schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    examples: ClassVar[tuple[Example, ...]] = (Example(call="echo()", result="ok"),)

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, content=str(args.get("content", "")))


class _Raises(_Ok):
    """A tool whose body escapes with whatever it is given."""

    name = "boom"

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        raise self._exc


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return context(tmp_path)


# --------------------------------------------------------------------------- #
# The truncation rule
# --------------------------------------------------------------------------- #


async def test_an_oversized_result_is_truncated_with_a_marker_naming_the_cut(
    ctx: ToolContext,
) -> None:
    """The universal net, not any one tool's own cap."""
    overflow = 500
    result = await _Ok().execute({"content": "x" * (MAX_RESULT_CHARS + overflow)}, ctx)

    assert result.ok, "truncation is not a failure — the content was still produced"
    assert "truncated" in result.content
    # The marker says *how much* was cut, not merely that something was.
    assert str(overflow) in result.content
    assert str(MAX_RESULT_CHARS) in result.content
    # The head survives intact, so what the model reads is a real prefix.
    assert result.content.startswith("x" * 100)


async def test_truncation_is_deterministic(ctx: ToolContext) -> None:
    """Same input, same bytes. A transcript that cannot be replayed is not a record."""
    payload = {"content": "y" * (MAX_RESULT_CHARS + 77)}
    first = await _Ok().execute(payload, ctx)
    second = await _Ok().execute(payload, ctx)
    assert first.content == second.content
    assert first == second


async def test_a_result_at_the_limit_is_left_alone(ctx: ToolContext) -> None:
    """Exactly at the cap is not over it — an off-by-one here truncates every result."""
    result = await _Ok().execute({"content": "z" * MAX_RESULT_CHARS}, ctx)
    assert "truncated" not in result.content
    assert len(result.content) == MAX_RESULT_CHARS


async def test_truncation_preserves_the_ok_flag(ctx: ToolContext) -> None:
    """A huge *successful* result must not become a failure by being long."""
    result = await _Ok().execute({"content": "q" * (MAX_RESULT_CHARS * 2)}, ctx)
    assert result.ok is True
    assert result.error == ""


# --------------------------------------------------------------------------- #
# Errors are values across the boundary
# --------------------------------------------------------------------------- #


async def test_a_tool_error_becomes_a_failed_result(ctx: ToolContext) -> None:
    result = await _Raises(ToolError("path is required")).execute({}, ctx)
    assert result.ok is False
    assert result.error == "path is required"


async def test_an_unanticipated_exception_becomes_a_failed_result(ctx: ToolContext) -> None:
    """An OSError from a vanished file is an observation, not a crash for the user."""
    result = await _Raises(OSError("ENOENT: gone")).execute({}, ctx)
    assert result.ok is False
    assert "boom failed unexpectedly" in result.error
    assert "OSError" in result.error
    assert "gone" in result.error


async def test_not_implemented_error_is_re_raised_rather_than_swallowed(
    ctx: ToolContext,
) -> None:
    """A tool that forgot to implement `run` is a programming bug, not an observation.

    Reporting it to the model as a failed result would teach it to retry something
    that can never work.
    """
    with pytest.raises(NotImplementedError):
        await _Raises(NotImplementedError("todo")).execute({}, ctx)


# --------------------------------------------------------------------------- #
# `spec()` refuses a half-written description
# --------------------------------------------------------------------------- #


def test_spec_requires_a_name_a_summary_and_when_to_use() -> None:
    class NoName(_Ok):
        name = ""

    class NoSummary(_Ok):
        summary = ""

    class NoWhen(_Ok):
        when_to_use = ""

    with pytest.raises(ValueError, match="must set a name"):
        NoName().spec()
    with pytest.raises(ValueError, match="must set a summary"):
        NoSummary().spec()
    with pytest.raises(ValueError, match="must say when to use it"):
        NoWhen().spec()


def test_spec_requires_at_least_one_worked_example() -> None:
    class NoExamples(_Ok):
        examples: ClassVar[tuple[Example, ...]] = ()

    with pytest.raises(ValueError, match="at least one worked example"):
        NoExamples().spec()


def test_a_well_formed_tool_produces_a_spec_the_loop_can_send() -> None:
    spec = _Ok().spec()
    assert spec.name == "echo"
    assert "WHEN TO USE" in spec.description
    assert spec.danger_level is DangerLevel.READ_ONLY
    assert spec.requires_approval is False


# --------------------------------------------------------------------------- #
# Argument coercion — the malformed-args surface every tool shares
# --------------------------------------------------------------------------- #


def test_require_str_refuses_missing_and_non_string_values() -> None:
    assert require_str({"path": "a.py"}, "path") == "a.py"
    with pytest.raises(ToolError, match="is required"):
        require_str({}, "path")
    with pytest.raises(ToolError, match="must be a string, got int"):
        require_str({"path": 7}, "path")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, 3), ("3", 3), ("-3", -3), (None, 99)],
    ids=["int", "str", "negative-str", "missing-uses-default"],
)
def test_optional_int_tolerates_the_string_form_models_send(value: object, expected: int) -> None:
    """Models routinely send `"10"` for a number; refusing that is a refusal the
    model cannot act on, so the coercion is deliberate rather than sloppy."""
    assert optional_int({"n": value} if value is not None else {}, "n", 99) == expected


@pytest.mark.parametrize("value", [True, "ten", 1.5, [], "3.5"])
def test_optional_int_refuses_what_is_not_an_integer(value: object) -> None:
    """`True` is rejected explicitly: bool is an int subclass, so a permissive
    check would silently read True as 1."""
    with pytest.raises(ToolError):
        optional_int({"n": value}, "n")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", True), ("FALSE", False), (" true ", True)],
    ids=["bool-true", "bool-false", "str-true", "upper-str-false", "padded"],
)
def test_optional_bool_tolerates_the_string_form(value: object, expected: bool) -> None:
    assert optional_bool({"flag": value}, "flag") is expected


def test_optional_bool_default_and_refusal() -> None:
    assert optional_bool({}, "flag") is False
    assert optional_bool({}, "flag", True) is True
    with pytest.raises(ToolError, match="must be a boolean"):
        optional_bool({"flag": "yes"}, "flag")


def test_an_empty_path_is_refused_before_it_becomes_the_root(ctx: ToolContext) -> None:
    """`resolve("")` would otherwise return the root itself, so a model that sent
    an empty path would silently operate on the whole tree."""
    with pytest.raises(ToolError, match="path is required"):
        ctx.resolve("")
    with pytest.raises(ToolError, match="path is required"):
        ctx.resolve("   ")
