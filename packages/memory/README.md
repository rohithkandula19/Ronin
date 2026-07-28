# ronin-memory

Three-layer memory module. Pick the layer(s) that fit your agent.

## Short-term (conversation history)

```python
from ronin_memory import ShortTermMemory

mem = ShortTermMemory(keep_recent=6, compress_threshold_tokens=4000)
mem.add_turn("user", "What's your name?")
mem.add_turn("assistant", "Claude.")

# Hand off to the LLM:
client.messages.create(
    model="claude-sonnet-4-6",
    messages=mem.messages(),
    max_tokens=1024,
)

# After enough turns, compress on the fly. Older turns become a rolling summary;
# the last `keep_recent` turns stay verbatim.
mem.maybe_compress()
```

## Long-term (persistent retrieval)

```python
from ronin_memory import LongTermMemory

mem = LongTermMemory()  # in-memory backend by default
mem.remember("user prefers dark mode", namespace="alice", source="onboarding")
hits = mem.recall("UI preferences", namespace="alice", k=3)
```

In-memory backend uses Jaccard scoring — fine for dev/tests. For persistent,
local-first memory, use the dependency-free SQLite backend:

```python
from ronin_memory import LongTermMemory, SqliteBackend

mem = LongTermMemory(backend=SqliteBackend("~/.ronin/memory.db"))
mem.remember("user prefers terse answers", namespace="alice")
```

`SqliteBackend` accepts an optional `score_fn(query, candidate) -> float`, so callers
can plug in local embeddings or a stronger ranker without changing the
`LongTermMemory` API. The package intentionally does not ship a ChromaDB dependency:
that extra was removed because it was unused and carried an unpatched advisory.

## User preferences (namespaced KV with extraction)

```python
from ronin_memory import UserPreferenceMemory

prefs = UserPreferenceMemory()
prefs.set("alice", "tone", "concise")

# Or let Claude extract durable preferences from a free-form message:
stored = prefs.extract_from_message("alice", "I'm in LA and I like short answers.")
# -> [("timezone", "America/Los_Angeles"), ("tone", "concise")]
```

## Tests

```bash
uv run --frozen pytest packages/memory -q
```

No API key needed — Claude calls are mocked.
