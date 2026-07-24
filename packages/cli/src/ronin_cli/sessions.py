"""Conversation history — every session archived, listable, resumable.

Previously ronin kept a single rolling file per project and **overwrote** it, so
only the latest conversation survived. This module archives **every** session to
its own timestamped file under ``.ronin/sessions/`` (full transcript, not capped),
so the whole history of every conversation is kept:

- ``ronin … --continue`` reloads the most recent session for the current project
  and *continues* it (writes back to the same file).
- ``/resume`` lists past sessions; ``/resume <n>`` reloads one.

Files live under ``.ronin/`` which is gitignored, so conversations stay local and
are never committed.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from pathlib import Path

_SESSIONS_SUBDIR = Path(".ronin") / "sessions"

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


SCHEMA_VERSION = 2


def _serialize_messages(messages: list | None) -> list:
    """Best-effort JSON-safe form of a structured Message list (pydantic objects
    or already-dicts). Never raises — a serialization hiccup must not lose the
    display transcript."""
    out: list = []
    for m in messages or []:
        if isinstance(m, dict):
            out.append(m)
        elif hasattr(m, "model_dump"):
            try:
                out.append(m.model_dump())
            except Exception:  # noqa: BLE001
                pass
    return out


def save_session(root: Path | str, transcript: list[str], *,
                 session_id: str | None = None, messages: list | None = None) -> Path | None:
    """Archive ``transcript`` (display) and, when given, the full structured
    ``messages`` (tool calls + results) so ``--continue`` restores real context,
    not just a flattened text tail. Atomic (tmp + replace). Returns the path."""
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
        "version": SCHEMA_VERSION,
        "id": sid,
        "root": str(Path(root).resolve()),
        "proj": _proj_key(root),
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "title": _title_of(transcript),
        "turns": sum(1 for e in transcript if e.startswith("USER: ")),
        "transcript": transcript,
        "messages": _serialize_messages(messages),
    }
    # Atomic write: a crash mid-save must never truncate the session (it used to
    # write_text in place → a partial file, silently loaded as empty next run).
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
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


import re as _re

# Session ids are our own timestamp-uuid stamps (or legacy ``code-<hash>``);
# never a path. Validate before building a filesystem path so a crafted id can't
# traverse out of the sessions dir (defends the path expression below).
_SAFE_SESSION_ID = _re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _read_session(session_id: str) -> dict | None:
    """Parse a session file. A corrupt file emits a one-line warning to stderr
    (never a silent empty result that looks like 'no history')."""
    if not session_id or not _SAFE_SESSION_ID.fullmatch(session_id):
        return None
    path = _dir() / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        import sys
        print(f"ronin: warning — session {session_id} is unreadable ({e}); "
              "starting fresh for this turn.", file=sys.stderr)
        return None


def load_session(session_id: str) -> list[str]:
    """The display transcript for a session id (empty list if missing/corrupt)."""
    data = _read_session(session_id)
    return list(data.get("transcript", [])) if data else []


def load_session_messages(session_id: str) -> list:
    """The full structured message list (Message objects) for a session, for real
    resume. Empty for legacy v1 files (which only stored the flat transcript) —
    callers fall back to the transcript tail in that case."""
    data = _read_session(session_id)
    if not data:
        return []
    raw = data.get("messages") or []
    if not raw:
        return []
    try:
        from ronin_agent_patterns import Message
        return [Message(**m) if isinstance(m, dict) else m for m in raw]
    except Exception:  # noqa: BLE001 — a schema drift must not break resume
        return []


def latest_session(root: Path | str) -> str | None:
    """Id of the most recent session for ``root`` (for --continue), or None."""
    sessions = list_sessions(root)
    return sessions[0]["id"] if sessions else None
