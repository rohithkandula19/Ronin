"""The integration layer: the only package that imports every other one.

Everything below this package is written against protocols and injected callables,
which is what let seven subsystems be built and tested independently. The cost of
that is that nothing below can check the *joins*, and a join is exactly where a
correct part becomes a wrong whole — a `TaintTracker` that nothing registers content
into escalates nothing; a repo map that never reaches ``pinned_prefix_tokens`` makes
compaction believe the window is 2k emptier than it is; a settings file that parses
into rules no policy engine ever sees is a config that silently does nothing.

So this package is deliberately thin and deliberately explicit:

* :mod:`~ronin.cli.spine` — where things live (:class:`~ronin.cli.spine.Paths`), what
  was read (:class:`~ronin.cli.spine.Loaded`), what is wired
  (:class:`~ronin.cli.spine.Runtime`), and every degradation named as a
  :class:`~ronin.cli.spine.Note` rather than logged and lost.

Nothing below ``cli`` may import it. That rule is enforced by
``tests/tools/test_boundaries.py``, which walks the import graph with ``ast`` so a
lazy import inside a function does not slip past.
"""
from __future__ import annotations

from .spine import (
    CACHE_SUBDIR,
    COMMANDS_SUBDIR,
    HOOKS_FILENAME,
    RONIN_DIRNAME,
    Closer,
    Loaded,
    Note,
    Paths,
    Runtime,
    notes_from_settings,
    sandbox_note,
    sequence_note,
)

__all__ = [
    "CACHE_SUBDIR",
    "COMMANDS_SUBDIR",
    "HOOKS_FILENAME",
    "RONIN_DIRNAME",
    "Closer",
    "Loaded",
    "Note",
    "Paths",
    "Runtime",
    "notes_from_settings",
    "sandbox_note",
    "sequence_note",
]
