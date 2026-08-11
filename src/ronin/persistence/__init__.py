"""Persistence and replay: the session that survives the process.

Four modules, one direction of dependency (``codec → transcript → resume →
export``), and nothing imported from outside ``ronin.core`` — this layer never
learns which provider produced the events it is writing down.

* :mod:`~ronin.persistence.codec` — every core value ⇄ a versioned, discriminated
  JSON record. Refuses another schema version by name instead of decoding half of
  it.
* :mod:`~ronin.persistence.transcript` — the append-only ``.ronin/sessions/<id>.jsonl``
  log, fsynced at turn boundaries, plus the sidecar that makes a session picker
  cheap. A torn final line loads with a report; a hole in the middle does not load.
* :mod:`~ronin.persistence.resume` — folding the recorded stream back into an
  ``AgentState``, honouring ``StreamReset``, reconciling against the recorded
  ``TurnEnd.agent_state``, and repairing a tool call the crash left unanswered so
  the transcript stays sendable.
* :mod:`~ronin.persistence.export` — pure ``(events, metadata) -> str`` exporters:
  Markdown, and one self-contained HTML file with the tool calls collapsed and
  everything escaped.

The whole layer, run end to end: ``python -m ronin.persistence.demo``.
"""

from __future__ import annotations

from .codec import (
    EVENT_TAGS,
    SCHEMA_VERSION,
    TYPE_KEY,
    VERSION_KEY,
    CodecError,
    MalformedRecord,
    SchemaVersionMismatch,
    UnknownEventType,
    UnknownRecordType,
    decode,
    decode_event,
    decode_state,
    encode,
    encode_event,
    encode_state,
    missing_event_codecs,
)
from .export import to_html, to_markdown
from .resume import (
    INTERRUPTED_NO_RESULT,
    ReplayError,
    ReplayResult,
    ReplayTurn,
    ResumedSession,
    StateSource,
    fold_turns,
    load_session,
    replay,
    resume_latest,
    resume_session,
    same_cwd,
    sessions_for_cwd,
)
from .transcript import (
    HEADER_TAG,
    SESSIONS_SUBDIR,
    ReadResult,
    SessionMeta,
    Transcript,
    TranscriptError,
    TranscriptVersionError,
    list_sessions,
    meta_path,
    new_session_id,
    read_events,
    session_path,
    sessions_dir,
    valid_session_id,
)

__all__ = [
    "EVENT_TAGS",
    "HEADER_TAG",
    "INTERRUPTED_NO_RESULT",
    "SCHEMA_VERSION",
    "SESSIONS_SUBDIR",
    "TYPE_KEY",
    "VERSION_KEY",
    "CodecError",
    "MalformedRecord",
    "ReadResult",
    "ReplayError",
    "ReplayResult",
    "ReplayTurn",
    "ResumedSession",
    "SchemaVersionMismatch",
    "SessionMeta",
    "StateSource",
    "Transcript",
    "TranscriptError",
    "TranscriptVersionError",
    "UnknownEventType",
    "UnknownRecordType",
    "decode",
    "decode_event",
    "decode_state",
    "encode",
    "encode_event",
    "encode_state",
    "fold_turns",
    "list_sessions",
    "load_session",
    "meta_path",
    "missing_event_codecs",
    "new_session_id",
    "read_events",
    "replay",
    "resume_latest",
    "resume_session",
    "same_cwd",
    "session_path",
    "sessions_dir",
    "sessions_for_cwd",
    "to_html",
    "to_markdown",
    "valid_session_id",
]
