"""Automatic context engineering — feed the model the right code up front.

An agent's quality is bounded by what's in its context window. Left to itself, a
model on a fresh repo burns several round-trips listing and grepping before it
finds the files that matter — slow (every round-trip is a full model call) and
error-prone. This module front-loads that: each turn it asks the repo map which
files are most relevant to the request and injects a compact pointer block
(paths + symbol outlines, *not* full contents — a few hundred tokens) into the
prompt, so the model starts already aimed at the right code.

Two properties make this safe to run every turn:
- **Non-blocking.** If the index isn't built yet, we kick off a background build
  and skip injection for this turn — it never adds first-turn latency. The next
  turn, the warm index is used.
- **Self-gating.** Only files with a real relevance score are included, so
  chit-chat ("hey") that matches nothing injects nothing.
"""
from __future__ import annotations

import threading
from pathlib import Path

from .repo_map import RepoIndex, cached_index, get_index

# Roots with a background build in flight — so we don't spawn duplicate builders.
_BUILDING: set[str] = set()
_LOCK = threading.Lock()


def _ensure_background_build(root: Path | str) -> None:
    key = str(Path(root).resolve())
    with _LOCK:
        if key in _BUILDING:
            return
        _BUILDING.add(key)

    def _build() -> None:
        try:
            get_index(root)
        except Exception:  # noqa: BLE001 — best-effort; never crash a turn
            pass
        finally:
            with _LOCK:
                _BUILDING.discard(key)

    threading.Thread(target=_build, daemon=True).start()


def format_context_block(results: list, *, max_files: int = 5) -> str | None:
    """Render ranked (score, doc) hits into a compact pointer block, or None if
    there's nothing worth injecting. Keeps only files near the top score so a
    weak, incidental match doesn't pad the prompt."""
    if not results:
        return None
    top = results[0][0]
    if top <= 0:
        return None
    keep = [(s, d) for s, d in results if s >= top * 0.35][:max_files]
    lines = [
        "## Relevant code (auto-retrieved by ronin's repo map)",
        "These files look most relevant to the request — read the ones you need "
        "instead of listing/searching blindly:",
    ]
    for _score, doc in keep:
        if doc.symbols:
            outline = ", ".join(name for name, _ in doc.symbols[:8])
            lines.append(f"- {doc.path} — {outline}")
        else:
            lines.append(f"- {doc.path}")
    return "\n".join(lines)


def relevant_context(query: str, root: Path | str = ".", *,
                     max_files: int = 5, build_sync: bool = False) -> str | None:
    """Compact, relevance-ranked context block for ``query``, or None.

    Non-blocking by default: a cold index triggers a background build and returns
    None for this turn. Pass ``build_sync=True`` to build inline (used in tests /
    one-shot contexts where there is no "next turn")."""
    if not query or len(query.strip()) < 3:
        return None
    idx: RepoIndex | None = cached_index(root)
    if idx is None:
        if build_sync:
            idx = get_index(root)
        else:
            _ensure_background_build(root)
            return None
    if not idx.docs:
        return None
    return format_context_block(idx.search(query, max_files), max_files=max_files)
