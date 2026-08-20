"""The summary command."""

from __future__ import annotations

from cli.emit import Emitter


def summary(counts: dict[str, int], emitter: Emitter) -> int:
    for key in sorted(counts):
        emitter.line("%s: %d" % (key, counts[key]))
    return 0
