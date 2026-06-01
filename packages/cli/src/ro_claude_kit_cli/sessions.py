"""Conversation history — every session archived, listable, resumable.

Previously ronin kept a single rolling file per project and **overwrote** it, so
only the latest conversation survived. This module archives **every** session to
its own timestamped file under ``.csk/sessions/`` (full transcript, not capped),
so the whole history of every conversation is kept:

- ``ronin … --continue`` reloads the most recent session for the current project
  and *continues* it (writes back to the same file).
- ``/resume`` lists past sessions; ``/resume <n>`` reloads one.

Files live under ``.csk/`` which is gitignored, so conversations stay local and
are never committed.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from pathlib import Path

_SESSIONS_SUBDIR = Path(".csk") / "sessions"

# One session id per process run (lazily created), so all saves within a single
# `ronin` invocation land in the same file — and a new run starts a new file.
_session_id: str | None = None


def _dir() -> Path:
    return _SESSIONS_SUBDIR


def _proj_key(root: Path | str) -> str:
    return hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:12]


def current_session_id() -> str:
    """The id for this process's session (created on first use)."""
    global _session_id
    if _session_id is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        _session_id = f"{stamp}-{uuid.uuid4().hex[:6]}"   # timestamp for ordering, uuid for uniqueness
    return _session_id


def set_current_session(session_id: str) -> None:
    """Continue an existing session (used by --continue and /resume) so further
    saves append to that session's file instead of forking a new one."""
    global _session_id
    _session_id = session_id


def _title_of(transcript: list[str]) -> str:
    for entry in transcript:
        if entry.startswith("USER: "):
            return entry[len("USER: "):].strip().replace("\n", " ")[:80]
    return "(no user message)"


def save_session(root: Path | str, transcript: list[str], *,
                 session_id: str | None = None) -> Path | None:
    """Archive ``transcript`` (in full) to this session's file. Returns the path."""
    if not transcript:
        return None
    sid = session_id or current_session_id()
    d = _dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    path = d / f"{sid}.json"
    data = {
        "id": sid,
        "root": str(Path(root).resolve()),
        "proj": _proj_key(root),
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "title": _title_of(transcript),
        "turns": sum(1 for e in transcript if e.startswith("USER: ")),
        "transcript": transcript,
    }
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return None
    return path


def list_sessions(root: Path | str | None = None) -> list[dict]:
    """Metadata for archived sessions, most-recent first. If ``root`` is given,
    only that project's sessions. Tolerates legacy ``code-*.json`` files."""
    d = _dir()
    if not d.is_dir():
        return []
    key = _proj_key(root) if root is not None else None
    out: list[dict] = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        transcript = data.get("transcript")
        if not isinstance(transcript, list):
            continue
        # legacy files (code-<hash>.json) lack the metadata fields — backfill.
        froot = data.get("root", "")
        proj = data.get("proj") or (_proj_key(froot) if froot else "")
        if key is not None and proj != key:
            continue
        out.append({
            "id": data.get("id", f.stem),
            "root": froot,
            "title": data.get("title") or _title_of(transcript),
            "turns": data.get("turns", sum(1 for e in transcript if e.startswith("USER: "))),
            "updated": data.get("updated", ""),
        })
    # id is timestamped too, so it's a stable tiebreaker when two saves land in
    # the same second (e.g. in tests).
    out.sort(key=lambda d: (d["updated"], d["id"]), reverse=True)
    return out


def load_session(session_id: str) -> list[str]:
    """The transcript for a session id (empty list if missing)."""
    path = _dir() / f"{session_id}.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("transcript", []))
    except (OSError, ValueError):
        return []


def latest_session(root: Path | str) -> str | None:
    """Id of the most recent session for ``root`` (for --continue), or None."""
    sessions = list_sessions(root)
    return sessions[0]["id"] if sessions else None
