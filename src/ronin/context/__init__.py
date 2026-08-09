"""The agent's memory of the world: what it knows, what it kept, what it cut.

Five pieces, each answering one way a long coding session dies.

* :mod:`~ronin.context.repomap` — the model never sees the whole repo. A pagerank
  over the import graph picks the important files and only their *signatures* are
  emitted, inside a hard token budget that degrades by dropping leaf files first
  and says how many it dropped.
* :mod:`~ronin.context.memory` — RONIN.md: build commands, test commands, style
  rules, gotchas. Global, then outermost project file to innermost, most local
  winning, every section labelled with where it came from.
* :mod:`~ronin.context.compaction` — at 80% of the window the middle of the
  transcript folds into a five-part summary. The system prompt, repo map, RONIN.md
  and the last three turns are pinned, and the most recent tool result per file
  path survives in full — which is what makes "what did we edit in turn 3"
  answerable two hundred turns later.
* :mod:`~ronin.context.filestate` — every file read is hashed over bytes, and
  every edit re-checks, so the model cannot clobber an edit the user made in their
  own editor while it was thinking.
* :mod:`~ronin.context.budget` — one deterministic truncator for tool output:
  head 60%, tail 40%, and a marker naming the true elided count.

Nothing here imports ``ronin.providers`` or ``ronin.tools``. The summarizer arrives
as an injected async callable and the parser as a protocol, which is why the whole
package tests offline with no model, no network and no tokenizer.

Every token figure this package produces is an estimate at ``len(text) / 4``. It is
labelled as an estimate everywhere it is rendered, because a number that looks
measured and is not is worse than no number.
"""
from __future__ import annotations

from .budget import (
    CHARS_PER_TOKEN,
    HEAD_FRACTION,
    Clamped,
    ClampMode,
    OutputBudget,
    char_marker,
    clamp,
    estimate_tokens,
    line_marker,
)
from .compaction import (
    COMPACTION_KEY,
    DEFAULT_PINNED_TAIL_TURNS,
    DEFAULT_TRIGGER_FRACTION,
    PATH_ARGUMENT_KEYS,
    PINNED_KEY,
    RETAINED_PATH_KEY,
    SUMMARY_INSTRUCTIONS,
    SUMMARY_SECTIONS,
    CompactionPlan,
    CompactionPolicy,
    CompactionResult,
    Summarizer,
    build_summary_prompt,
    compact,
    file_path_from_arguments,
    latest_tool_result_per_path,
    maybe_compact,
    message_tokens,
    missing_sections,
    plan_compaction,
    render_message,
    repair_pairing,
    should_compact,
    transcript_tokens,
)
from .filestate import (
    BytesReader,
    FileCheck,
    FileStateTracker,
    FileStatus,
    ReadRecord,
    digest_bytes,
)
from .memory import (
    BOOTSTRAP_SOURCES,
    GLOBAL_SUBPATH,
    REMEMBERED_HEADING,
    RONIN_FILENAME,
    CommandKind,
    FoundCommand,
    Memory,
    MemoryFile,
    MemoryScope,
    discover_commands,
    find_repo_root,
    load_memory,
    memory_paths,
    propose_bootstrap,
    remember,
)
from .repomap import (
    DEFAULT_BUDGET_TOKENS,
    DEFAULT_TOP_N,
    FileEntry,
    GitIgnore,
    IgnoreRule,
    ImportGraph,
    ImportRef,
    Parser,
    ParserUnavailable,
    PythonAstParser,
    RefKind,
    RepoMap,
    Signature,
    SignatureKind,
    TreeSitterLoader,
    TreeSitterParser,
    build_import_graph,
    build_repo_map,
    default_tree_sitter_loader,
    pagerank,
    python_imports,
    script_imports,
    walk_repo,
)

__all__ = [
    "BOOTSTRAP_SOURCES",
    "CHARS_PER_TOKEN",
    "COMPACTION_KEY",
    "DEFAULT_BUDGET_TOKENS",
    "DEFAULT_PINNED_TAIL_TURNS",
    "DEFAULT_TOP_N",
    "DEFAULT_TRIGGER_FRACTION",
    "GLOBAL_SUBPATH",
    "HEAD_FRACTION",
    "PATH_ARGUMENT_KEYS",
    "PINNED_KEY",
    "REMEMBERED_HEADING",
    "RETAINED_PATH_KEY",
    "RONIN_FILENAME",
    "SUMMARY_INSTRUCTIONS",
    "SUMMARY_SECTIONS",
    "BytesReader",
    "ClampMode",
    "Clamped",
    "CommandKind",
    "CompactionPlan",
    "CompactionPolicy",
    "CompactionResult",
    "FileCheck",
    "FileEntry",
    "FileStateTracker",
    "FileStatus",
    "FoundCommand",
    "GitIgnore",
    "IgnoreRule",
    "ImportGraph",
    "ImportRef",
    "Memory",
    "MemoryFile",
    "MemoryScope",
    "OutputBudget",
    "Parser",
    "ParserUnavailable",
    "PythonAstParser",
    "ReadRecord",
    "RefKind",
    "RepoMap",
    "Signature",
    "SignatureKind",
    "Summarizer",
    "TreeSitterLoader",
    "TreeSitterParser",
    "build_import_graph",
    "build_repo_map",
    "build_summary_prompt",
    "char_marker",
    "clamp",
    "compact",
    "default_tree_sitter_loader",
    "digest_bytes",
    "discover_commands",
    "estimate_tokens",
    "file_path_from_arguments",
    "find_repo_root",
    "latest_tool_result_per_path",
    "line_marker",
    "load_memory",
    "maybe_compact",
    "memory_paths",
    "message_tokens",
    "missing_sections",
    "pagerank",
    "plan_compaction",
    "propose_bootstrap",
    "python_imports",
    "remember",
    "render_message",
    "repair_pairing",
    "script_imports",
    "should_compact",
    "transcript_tokens",
    "walk_repo",
]
