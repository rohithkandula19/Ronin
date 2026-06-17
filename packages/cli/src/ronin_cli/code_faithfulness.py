"""Wire the faithfulness edit guard into the coding agent's live write path.

``faithfulness_hook`` holds the mode-aware grounding logic (``evaluate_edit``);
``ronin_hardening.faithfulness`` holds the harness itself. This module is the
thin coding-agent wiring that makes those actually fire when ``csk code`` writes
a file:

1. As the agent works, every read-tool result (``read_file``, ``search_files``,
   ``list_files``, ``glob``, ``grep``) is collected as a :class:`Source` - the
   evidence the agent actually observed. This mirrors ``sources_from_trace`` but
   accumulates live off the ``after_tool`` hook instead of a finished trace, so
   the guard can run *before* the write commits.
2. When the agent proposes a write/edit (``write_file`` / ``edit_file`` /
   ``multi_edit``), the guard pulls the proposed new code out of the tool args
   and scores it against everything read so far with ``evaluate_edit``.
3. In ``warn`` mode it surfaces the score (non-blocking) and lets the existing
   approval gate proceed. In ``gate`` mode an ungrounded edit (a write that
   references a symbol absent from every file the agent opened) is *held*: the
   agent is told the edit looks ungrounded so it must justify or revise, even
   under ``--yolo`` where the normal approval gate would auto-approve.

The guard never re-implements grounding; it reuses ``evaluate_edit`` so the same
harness powers ``csk code``, ``ronin agent --faithfulness``, and
``ronin faithfulness check``. It also never crashes a real run: any internal
error degrades to "no opinion" and the normal gate handles the write.
"""
from __future__ import annotations

from typing import Any, Callable

from ronin_hardening import Source

from .faithfulness_hook import (
    MODE_GATE,
    MODE_OFF,
    MODE_WARN,
    FaithfulnessOutcome,
    evaluate_edit,
    render,
)

# Write/edit tools whose new code we score against the read sources. These are a
# subset of code_tools.SENSITIVE_TOOLS - run_command / run_background / rewind do
# not propose file *content* to ground, so the edit guard skips them.
EDIT_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "multi_edit"})

# Read tools whose results are evidence the agent observed (mirrors the hardening
# package's evidence-tool set, extended with the coding agent's own read tools).
_EVIDENCE_TOOLS: frozenset[str] = frozenset(
    {"read_file", "search_files", "list_files", "glob", "grep", "repo_map",
     "web_search", "fetch_url", "web_fetch", "fetch"}
)


def proposed_code(name: str, args: dict) -> str:
    """Pull the proposed new code out of an edit tool's arguments.

    ``write_file`` carries ``content``; ``edit_file`` carries ``new_string``;
    ``multi_edit`` carries a list of ``{old_string, new_string}`` edits. We score
    the *new* text the agent wants to introduce - that is what must be grounded in
    the files it read. Returns "" when there is nothing to score.
    """
    if name == "write_file":
        return str(args.get("content", "") or "")
    if name == "edit_file":
        return str(args.get("new_string", "") or "")
    if name == "multi_edit":
        parts = [str(e.get("new_string", "") or "") for e in (args.get("edits") or [])]
        return "\n".join(p for p in parts if p)
    return ""


class EditGuard:
    """Accumulates read sources and scores proposed edits against them.

    One instance lives for the duration of a coding turn. ``collect`` is fed every
    tool result (wire it into the ``after_tool`` chain); ``evaluate`` scores a
    proposed edit against everything collected so far.
    """

    def __init__(self, *, mode: str, config: Any | None = None) -> None:
        self.mode = (mode or MODE_OFF).strip().lower()
        self.config = config
        self.sources: list[Source] = []
        # The most recent outcome, for callers/tests that want to inspect it.
        self.last_outcome: FaithfulnessOutcome | None = None

    @property
    def active(self) -> bool:
        return self.mode in (MODE_WARN, MODE_GATE)

    def collect(self, name: str, args: dict, result: Any, is_error: bool) -> None:
        """Record a read-tool result as a source (skip writes + errored calls)."""
        if is_error or name not in _EVIDENCE_TOOLS:
            return
        text = result if isinstance(result, str) else _stringify(result)
        if text and text.strip():
            self.sources.append(Source(origin=str(name), text=text))

    def evaluate(self, name: str, args: dict) -> FaithfulnessOutcome | None:
        """Score the proposed edit named ``name`` against the collected sources.

        Returns the outcome, or ``None`` when there is nothing to score (not an
        edit tool, empty content, or the harness errored - never crashes a run).
        """
        if name not in EDIT_TOOLS:
            return None
        code = proposed_code(name, args)
        if not code.strip():
            return None
        try:
            outcome = evaluate_edit(
                code, list(self.sources), mode=self.mode, config=self.config
            )
        except Exception:  # noqa: BLE001 - the guard must never break a real edit
            return None
        self.last_outcome = outcome
        return outcome


def _stringify(value: Any) -> str:
    import json

    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def gate_feedback(outcome: FaithfulnessOutcome) -> str:
    """The reject-with-feedback message handed to the model when an edit is held.

    The ReAct gate contract treats any non-empty string from ``before_tool`` as a
    denial-with-reason, so returning this string *holds* the edit and tells the
    agent why - it must justify or revise, not blindly retry. ASCII only.
    """
    r = outcome.report
    bits = [
        "faithfulness gate: this edit looks ungrounded in the files you have read",
        f"(score {r.score:.2f}).",
    ]
    if r.hallucinated_symbols:
        bits.append(
            "symbols not found in any source you read: "
            + ", ".join(r.hallucinated_symbols[:8])
            + "."
        )
    if r.abstain:
        bits.append(f"reason: {r.reason}.")
    bits.append(
        "Read the file(s) that define these symbols first, or revise the edit to "
        "use only what the sources actually contain, then propose it again."
    )
    return " ".join(bits)


def wrap_before_tool(
    inner: Callable[[str, dict], "bool | str"],
    guard: EditGuard,
    *,
    console: Any | None = None,
) -> Callable[[str, dict], "bool | str"]:
    """Wrap an existing ``before_tool`` gate with the faithfulness edit guard.

    For an edit tool the guard runs first:
    - ``warn``: surface the score (when there is something to flag) and fall
      through to the existing approval gate.
    - ``gate``: when the edit is ungrounded or the harness abstains, surface the
      panel and HOLD it (return reject-with-feedback) so the agent must revise -
      even under ``--yolo``, where the inner gate would auto-approve. A clean,
      grounded edit falls through to the normal gate untouched.

    Non-edit tools (and inactive modes) pass straight through to ``inner``.
    """
    if not guard.active:
        return inner

    def before_tool(name: str, args: dict) -> "bool | str":
        if name in EDIT_TOOLS:
            outcome = guard.evaluate(name, args)
            if outcome is not None:
                if outcome.should_warn:
                    render(outcome, console)
                if guard.mode == MODE_GATE and outcome.should_gate:
                    # Hold the ungrounded edit regardless of yolo/auto-approve.
                    return gate_feedback(outcome)
        return inner(name, args)

    return before_tool


def wrap_after_tool(
    inner: Callable[[str, dict, Any, bool], None] | None,
    guard: EditGuard,
) -> Callable[[str, dict, Any, bool], None]:
    """Wrap an existing ``after_tool`` hook so read results feed the guard.

    The guard's source collection runs first (so a write gated *after* a read in
    the same batch still sees that read), then the inner hook (user
    format/test hooks) runs as before. A guard error never breaks the run.
    """
    def after_tool(name: str, args: dict, result: Any, is_error: bool) -> None:
        try:
            guard.collect(name, args, result, is_error)
        except Exception:  # noqa: BLE001 - collection must never break the run
            pass
        if inner is not None:
            inner(name, args, result, is_error)

    return after_tool
