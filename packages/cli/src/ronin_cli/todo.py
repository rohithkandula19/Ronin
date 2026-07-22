"""A live task checklist for ``ronin code`` — the Claude-Code todo experience.

For any multi-step task the agent calls the ``update_todos`` tool to lay out a
plan and keep it current: exactly one item ``in_progress`` at a time, items
flipped to ``completed`` as it finishes them. The CLI renders the list as a
checklist that updates in place, so a long task is legible instead of a black
box.

The store holds the latest todos; the renderer draws them. Both the tool's
arguments and the store carry the same shape, so rendering can read straight
from the tool call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ronin_agent_patterns import Tool

VALID_STATUS = {"pending", "in_progress", "completed", "blocked", "failed"}

_STATUS_GLYPH = {
    "completed": "[green]✓[/green]",
    "in_progress": "[yellow]▶[/yellow]",
    "pending": "[dim]☐[/dim]",
    "blocked": "[#e0af68]⊘[/#e0af68]",
    "failed": "[#f7768e]✗[/#f7768e]",
}


class TodoStore:
    """Holds the current todo list for one agent run."""

    def __init__(self) -> None:
        self.todos: list[dict[str, str]] = []

    def replace(self, todos: list[dict[str, Any]]) -> None:
        cleaned: list[dict[str, str]] = []
        for t in todos:
            content = str(t.get("content", "")).strip()
            status = str(t.get("status", "pending"))
            if status not in VALID_STATUS:
                status = "pending"
            if content:
                cleaned.append({"content": content, "status": status})
        self.todos = cleaned

    def summary(self) -> str:
        done = sum(1 for t in self.todos if t["status"] == "completed")
        s = f"{done}/{len(self.todos)} done"
        blocked = sum(1 for t in self.todos if t["status"] == "blocked")
        failed = sum(1 for t in self.todos if t["status"] == "failed")
        if blocked:
            s += f", {blocked} blocked"
        if failed:
            s += f", {failed} failed"
        return s


def build_todo_tool(store: TodoStore) -> Tool:
    """A tool the agent calls to create/update its task checklist."""

    def update_todos(todos: list[dict[str, Any]]) -> str:
        store.replace(todos)
        return f"Todo list updated ({store.summary()})."

    return Tool(
        name="update_todos",
        description=(
            "Create or update your task checklist for a multi-step task. Pass the "
            "FULL list every time (it replaces the previous one). Keep exactly one "
            "item 'in_progress'; mark items 'completed' as you finish them. Use "
            "'blocked' for a step waiting on something (a missing dep, the user) and "
            "'failed' for a step that genuinely failed — never mark 'completed' to "
            "fake progress. Use this for any task with 3+ steps so progress is visible."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "The task, imperative and short."},
                            "status": {"type": "string", "enum": sorted(VALID_STATUS)},
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["todos"],
        },
        handler=update_todos,
    )


def render_todos(console, todos: list[dict[str, Any]]) -> None:
    """Draw the checklist (used by the live renderer when update_todos is called)."""
    if not todos:
        return
    console.print("  [bold]Plan[/bold]")
    for t in todos:
        status = t.get("status", "pending")
        glyph = _STATUS_GLYPH.get(status, "[dim]☐[/dim]")
        content = str(t.get("content", ""))
        if status == "completed":
            console.print(f"  {glyph} [dim strike]{content}[/dim strike]")
        elif status == "in_progress":
            console.print(f"  {glyph} [bold]{content}[/bold]")
        elif status == "failed":
            console.print(f"  {glyph} [#f7768e]{content}[/#f7768e]")
        elif status == "blocked":
            console.print(f"  {glyph} [#e0af68]{content}[/#e0af68] [dim](blocked)[/dim]")
        else:
            console.print(f"  {glyph} [dim]{content}[/dim]")


# ---- marker -> GitHub-issue draft -----------------------------------------
#
# `ronin todo` lists every FIXME/TODO/HACK in the tree. `--issues` turns the ones
# you pick into draft GitHub issues (via the gh helper) so a stray "TODO: handle
# the timeout" becomes a tracked piece of work instead of rotting in a comment.
# The marker -> draft mapping is a pure function (below); the gh call lives in
# gh_helper. Dry-run by default — nothing is filed without explicit confirmation.

_MARKER_TITLE = {"FIXME": "Fix", "TODO": "Do", "HACK": "Clean up", "XXX": "Revisit"}


@dataclass
class IssueDraft:
    """A GitHub issue drafted from one marker comment."""
    title: str
    body: str
    labels: list[str]


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def context_lines(lines: list[str], line: int, before: int = 3, after: int = 3) -> list[str]:
    """The ``before``/``after`` source lines around 1-based ``line``. Pure.

    Returns the slice as-is (no markers added) so it can drop straight into a
    fenced code block. Clamps to the file bounds; empty list for an out-of-range
    line or empty file."""
    if not lines or line < 1 or line > len(lines):
        return []
    start = max(0, line - 1 - before)
    end = min(len(lines), line + after)
    return lines[start:end]


def marker_to_issue(file: str, line: int, marker: str, text: str,
                    context: list[str] | None = None) -> IssueDraft:
    """Turn one marker comment into a GitHub-issue draft. Pure and deterministic.

    ``file``/``line``/``marker``/``text`` come straight from kaizen.find_targets;
    ``context`` is the optional surrounding source lines (rendered as a fenced
    code block). The title summarizes the marker; the body carries the file:line
    reference and the code context so the issue is actionable on its own.
    """
    marker = (marker or "TODO").upper()
    verb = _MARKER_TITLE.get(marker, "Address")
    summary = _clip(text or "", 60) or f"{marker} in {file}"
    title = f"[{marker}] {verb}: {summary}"

    loc = f"`{file}:{line}`"
    parts = [f"From a `{marker}` marker at {loc}:", "", f"> {_clip(text or '', 200)}"]
    if context:
        body_ctx = "\n".join(context).rstrip("\n")
        parts += ["", "Context:", "", "```", body_ctx, "```"]
    parts += ["", "_Filed by `ronin todo --issues`._"]
    label = "fixme" if marker == "FIXME" else marker.lower()
    return IssueDraft(title=_clip(title, 120), body="\n".join(parts),
                      labels=["ronin", label])
