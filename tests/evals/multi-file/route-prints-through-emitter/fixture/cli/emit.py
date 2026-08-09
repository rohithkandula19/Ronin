"""Where output goes. Injected so a test can read what a command said."""

from __future__ import annotations


class Emitter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def line(self, text: str) -> None:
        self.lines.append(text)
