"""Self-verify: run the repo's tests before claiming an edit task is done.

The coding agent used to end a turn by *asserting* the work was done. This
module makes it stop claiming success blindly. After an edit task in
``run_code_agent`` (not read-only, and only when the agent actually changed
something), it:

1. Detects this repo's test/verify command - reusing ``verify_cmd`` detection
   (pytest / npm test / cargo / go / make / an explicit ``verify:`` line), the
   same engine the ``/verify`` and ``/fix`` commands already use. No new guesser.
2. RUNS it once, bounded by a sane timeout.
3. Appends a clear VERIFICATION verdict to the agent's summary - what ran,
   pass/fail, and if it failed, that the task is NOT confirmed done. The agent's
   own summary is kept; the verdict is added below it.

When no test command is found it says so honestly ("no tests found to verify")
rather than implying the work is verified.

It is bounded (exactly one verify run, one timeout) and opt-out-able via the
``RONIN_SELF_VERIFY`` env var (``0``/``off``/``false``/``no``/``disable``) or a
falsey ``self_verify`` attribute on the config. ON by default for edit tasks.

Everything here is pure except the single ``run_verify`` shell-out, and any
internal error degrades to "no note" so this never breaks a real run.
"""
from __future__ import annotations

import os
from typing import Any

# Edit tools whose presence in the trace means the agent actually changed code,
# so there is something to verify. Mirrors the edit-tool set used elsewhere
# (code_faithfulness / grounding_check) without importing it.
_EDIT_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "multi_edit"})

# One verify run, bounded. Long enough for a real suite, short enough that a
# hung command cannot wedge the turn.
DEFAULT_TIMEOUT = 300


def self_verify_enabled(config: Any | None = None) -> bool:
    """Whether the post-edit self-verify runs. ON by default for edit tasks.

    Opt out via the ``RONIN_SELF_VERIFY`` env var (``0``/``off``/``false``/
    ``no``/``disable``/empty) or a falsey ``self_verify`` attribute on the
    config. The env var wins so one export disables it everywhere.
    """
    raw = os.environ.get("RONIN_SELF_VERIFY")
    if raw is not None:
        return raw.strip().lower() not in ("0", "off", "false", "no", "disable", "")
    return bool(getattr(config, "self_verify", True))


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def changes_were_made(trace: list[Any]) -> bool:
    """True if the agent ran an edit tool this turn (something to verify).

    We look for an edit tool *call* in the trace; a read-only turn that never
    wrote anything has nothing to verify, so self-verify stays silent there.
    """
    for step in trace or []:
        if _attr(step, "kind") != "tool_call":
            continue
        content = _attr(step, "content")
        name = content.get("name") if isinstance(content, dict) else _attr(content, "name")
        if name in _EDIT_TOOLS:
            return True
    return False


def render_verdict(verdict: Any) -> str:
    """Format a :class:`verify_cmd.Verdict` into the appended VERIFICATION note.

    Three honest outcomes:
    - no command detected -> "no tests found to verify" (not a pass).
    - ran and passed       -> "verified" with the command that ran.
    - ran and failed (or could not run) -> "NOT verified" + that the task is not
      confirmed done.
    ASCII only, no hype.
    """
    ran = bool(_attr(verdict, "ran"))
    passed = bool(_attr(verdict, "passed"))
    command = str(_attr(verdict, "command") or "")
    summary = str(_attr(verdict, "summary") or "")

    if not ran:
        # ran=False covers both "no command detected" and "could not run it".
        if not command:
            return ("VERIFICATION: no tests found to verify this change. "
                    "The change is unverified.")
        return (f"VERIFICATION: NOT verified. Could not run `{command}` "
                f"({summary}). The task is NOT confirmed done.")
    if passed:
        return (f"VERIFICATION: verified. Ran `{command}` and it passed "
                f"({summary}).")
    return (f"VERIFICATION: NOT verified. Ran `{command}` and it FAILED "
            f"({summary}). The task is NOT confirmed done; fix the failing "
            "tests before relying on this.")


def verification_note(
    trace: list[Any],
    *,
    root: str | os.PathLike = ".",
    config: Any | None = None,
    read_only: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    run_verify: Any | None = None,
) -> str:
    """The VERIFICATION note to append after an edit task, or "" to add nothing.

    Returns "" when self-verify is disabled, the run was read-only, or the agent
    made no changes (nothing to verify). Otherwise it runs the repo's verify
    command ONCE and returns a one-paragraph verdict. ``run_verify`` is injected
    for tests (mock the runner); production passes the real one. Never raises.
    """
    try:
        if read_only or not self_verify_enabled(config):
            return ""
        if not changes_were_made(trace):
            return ""
        if run_verify is None:
            from .verify_cmd import run_verify as run_verify  # lazy: shells out
        verdict = run_verify(root, timeout=timeout)
        return render_verdict(verdict)
    except Exception:  # noqa: BLE001 - self-verify must never break a run
        return ""


def append_verification_note(output: str, note: str) -> str:
    """Append ``note`` below ``output`` on its own line. No-op when empty.

    The agent's own summary is preserved; the verdict is added, never overwrites.
    """
    if not note:
        return output
    if not output:
        return note
    return f"{output}\n\n{note}"
