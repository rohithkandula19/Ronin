"""The scan command."""

from __future__ import annotations

from cli.emit import Emitter


def scan(paths: list[str], emitter: Emitter) -> int:
    for path in paths:
        emitter.line("scanning %s" % path)
    emitter.line("scanned %d path(s)" % len(paths))
    return 0
