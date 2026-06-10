"""Three-layer memory module for Claude agents.

Layers:
- ``ShortTermMemory`` — conversation turns with smart compression (summarize older,
  keep recent verbatim).
- ``LongTermMemory`` — pluggable vector-store wrapper. Default backend is in-memory
  with Jaccard scoring; install the ``chromadb`` extra for production.
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
