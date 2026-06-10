"""Replay — read a past session back as a readable story.

ronin already archives every session as a USER:/ASSISTANT: transcript. This turns
one back into something you can actually skim: paired turns, trimmed, rendered.
Useful for "what did I do in here last week" and for sharing a session with a
teammate. The transcript→turns parse is pure and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Turn:
    role: str    # "user" | "assistant"
    text: str


def transcript_to_turns(transcript: list[str]) -> list[Turn]:
    """Parse a ``["USER: …", "ASSISTANT: …", …]`` transcript into turns.

    Lines without a known prefix are folded into the previous turn (multi-line
    messages survive). Pure."""
    turns: list[Turn] = []
    for entry in transcript:
        if entry.startswith("USER: "):
            turns.append(Turn("user", entry[len("USER: "):]))
        elif entry.startswith("ASSISTANT: "):
            turns.append(Turn("assistant", entry[len("ASSISTANT: "):]))
        elif turns:
            turns[-1] = Turn(turns[-1].role, turns[-1].text + "\n" + entry)
    return turns


def session_to_markdown(turns: list[Turn], *, title: str = "ronin session") -> str:
    """Render turns as a shareable markdown transcript. Pure."""
    lines = [f"# {title}", "", f"*Exported from a ronin session — {len(turns)} entries.*", ""]
    n = 0
    for t in turns:
        body = t.text.strip()
        if t.role == "user":
            n += 1
            lines += [f"## Turn {n} — you", "", body, ""]
        else:
            lines += ["**ronin:**", "", body, ""]
    return "\n".join(lines).rstrip() + "\n"


def render_story(console, turns: list[Turn], *, max_chars: int = 1200) -> None:
    """Print the turns as a readable back-and-forth."""
    if not turns:
        console.print("[dim](empty session)[/dim]")
        return
    for i, t in enumerate(turns, 1):
        body = t.text.strip()
        if len(body) > max_chars:
            body = body[:max_chars] + f"… [dim](+{len(body) - max_chars} chars)[/dim]"
        if t.role == "user":
            console.print(f"\n[bold #7aa2f7]▷ you[/bold #7aa2f7]  [dim]turn {(i + 1) // 2}[/dim]")
            console.print(f"  {body}")
        else:
            console.print("[bold #bb9af7]◁ ronin[/bold #bb9af7]")
            console.print(f"  [#c0caf5]{body}[/#c0caf5]")
