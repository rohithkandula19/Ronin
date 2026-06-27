"""Save / load PipelineState for ``--save-state`` and ``--resume``.

Resume is conservative: completed stages are not re-run (unless
``--rerun-completed``), artifacts/summaries/verdicts are preserved, and a
corrupt or incompatible file fails loudly with a clear message rather than
silently starting over.
"""
from __future__ import annotations

from pathlib import Path

from .pipeline import COMPLETED, PipelineState


class PipelineStateError(Exception):
    """Raised when a saved pipeline state can't be read/parsed/validated."""


def save_state(state: PipelineState, path) -> Path:
    """Write the state as pretty JSON (creating parent dirs). Returns the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return p


def load_state(path) -> PipelineState:
    """Load + validate a saved state. Raises :class:`PipelineStateError` clearly
    on a missing / corrupt / incompatible file."""
    p = Path(path)
    if not p.is_file():
        raise PipelineStateError(f"no saved pipeline state at {p}")
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise PipelineStateError(f"can't read {p}: {exc}") from exc
    try:
        state = PipelineState.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 — JSON decode or schema mismatch
        raise PipelineStateError(
            f"corrupt or incompatible pipeline state in {p}: {exc}") from exc
    if not state.roles or len(state.stages) != len(state.roles):
        raise PipelineStateError(
            f"incompatible pipeline state in {p}: roles/stages mismatch "
            f"({len(state.roles)} roles, {len(state.stages)} stages)")
    if [s.role for s in state.stages] != state.roles:
        raise PipelineStateError(
            f"incompatible pipeline state in {p}: stage roles don't match the role order")
    return state


def first_incomplete(state: PipelineState, *, rerun_completed: bool = False) -> int | None:
    """Index of the first stage to (re)run on resume, or None if all are done.

    Resumable = anything not 'completed'; with ``rerun_completed`` even completed
    stages re-run (so the first index is returned)."""
    for i, s in enumerate(state.stages):
        if rerun_completed or s.status != COMPLETED:
            return i
    return None
