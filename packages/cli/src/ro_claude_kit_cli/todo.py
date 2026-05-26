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

from typing import Any

from ro_claude_kit_agent_patterns import Tool

VALID_STATUS = {"pending", "in_progress", "completed"}

_STATUS_GLYPH = {
    "completed": "[green]✓[/green]",
    "in_progress": "[yellow]▶[/yellow]",
    "pending": "[dim]☐[/dim]",
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
        return f"{done}/{len(self.todos)} done"


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
            "item 'in_progress'; mark items 'completed' as you finish them. Use this "
            "for any task with 3+ steps so progress is visible."
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
        else:
            console.print(f"  {glyph} [dim]{content}[/dim]")
