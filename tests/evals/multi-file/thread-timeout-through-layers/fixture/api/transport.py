"""The bottom of the fetch stack. Records calls instead of using a socket."""

from __future__ import annotations


class Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def send(self, url: str, timeout: float = 30.0) -> str:
        self.calls.append((url, timeout))
        return "body of " + url
