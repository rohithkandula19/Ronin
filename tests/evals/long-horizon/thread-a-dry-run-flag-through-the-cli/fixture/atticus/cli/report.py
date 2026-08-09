"""What a run says it did.

Three kinds of line, and the distinction is the point:

* ``did`` — the disk changed, and here is how.
* ``plan`` — the disk did not change, and here is what would have happened.
* ``note`` — context, neither of the above.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Report:
    performed: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def did(self, action: str) -> None:
        self.performed.append(action)

    def plan(self, action: str) -> None:
        self.planned.append(action)

    def note(self, message: str) -> None:
        self.notes.append(message)

    def render(self, verbose: bool = False) -> str:
        lines: list[str] = []
        for action in self.planned:
            lines.append(f"would {action}")
        for action in self.performed:
            lines.append(action)
        for message in self.notes:
            lines.append(f"note: {message}")
        if verbose or not lines:
            lines.append(
                f"summary: {len(self.performed)} performed, {len(self.planned)} planned"
            )
        return "\n".join(lines)
