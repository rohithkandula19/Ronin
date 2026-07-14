"""Three-layer memory module for Claude agents.

Layers:
- ``ShortTermMemory`` — conversation turns with smart compression (summarize older,
  keep recent verbatim).
- ``LongTermMemory`` — pluggable vector-store wrapper. The default backend is
  in-memory with Jaccard scoring; the ``LongTermMemory`` interface accepts any
  backend you supply. (A ``chromadb`` extra was dropped: it was unused, and
  chromadb ships an unpatched pre-auth code-injection advisory — do not add it back
  until a fixed release exists.)
- ``UserPreferenceMemory`` — namespaced key-value store with Claude-driven fact
  extraction from free-form messages.
"""
from .long_term import InMemoryBackend, LongTermMemory, MemoryRecord
from .preferences import UserPreferenceMemory
from .short_term import ShortTermMemory, Turn

__all__ = [
    "InMemoryBackend",
    "LongTermMemory",
    "MemoryRecord",
    "ShortTermMemory",
    "Turn",
    "UserPreferenceMemory",
]
