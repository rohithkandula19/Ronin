"""The agent's body: the tools it acts through.

Precision over breadth. Fourteen tools, each with a description written as a prompt
(when to use, when *not* to use, a worked example) because half of agent quality is in
those strings, and each with the narrowest behaviour that does the job.

The three rules that carry the most weight:

1. **``write`` refuses a file that has not been read this session.** One rule, and it
   prevents most destructive edits — a model that has read the file knows what it is
   replacing, and one that has not is guessing.
2. **``edit`` matches exactly, and ambiguity is an error.** No fuzzy matching, no
   guessing. Two matches with ``replace_all=False`` fails and says how many there
   were, because that is a repair the model can actually make.
3. **``bash`` is one persistent shell.** ``cd``, ``export`` and an activated venv
   survive across calls, which is the difference between a model that sets up its
   environment once and one that forgets it every command.

Layout:

* :mod:`~ronin.tools.base` — what a tool is, and the session state the write guard needs
* :mod:`~ronin.tools.files` — read, write, edit, multi_edit
* :mod:`~ronin.tools.search` — glob, grep, ls (wrapping ripgrep, not reimplementing it)
* :mod:`~ronin.tools.shell` — bash, bash_output
* :mod:`~ronin.tools.task` — todo_write, task
* :mod:`~ronin.tools.net` — web_fetch, web_search
* :mod:`~ronin.tools.registry` — the ``ToolRegistry`` the loop is handed

Nothing here imports ``ronin.providers`` or ``ronin.core.loop``. A tool that knew
which model was calling it would special-case for that model, and the subagent runner
is injected for the same reason — the tool layer must not reach back into the loop.
"""
from __future__ import annotations

from .base import (
    MAX_RESULT_CHARS,
    Example,
    Tool,
    ToolContext,
    ToolError,
)
from .files import (
    MAX_READ_LINES,
    EditTool,
    MultiEditTool,
    ReadTool,
    WriteTool,
    apply_edit,
    number_lines,
)
from .net import (
    FetchCache,
    WebFetchTool,
    WebSearchTool,
    html_to_markdown,
)
from .registry import (
    ToolRegistry,
    build_registry,
    file_tools,
    search_tools,
    shell_tools,
)
from .search import GlobTool, GrepTool, LsTool
from .shell import (
    BashOutputTool,
    BashTool,
    PersistentShell,
    ShellSession,
    check_command,
    clamp_output,
)
from .task import (
    BUILTIN_SUBAGENTS,
    SubagentType,
    TaskTool,
    TodoWriteTool,
    parse_todos,
    render_todos,
)

__all__ = [
    "BUILTIN_SUBAGENTS",
    "MAX_READ_LINES",
    "MAX_RESULT_CHARS",
    "BashOutputTool",
    "BashTool",
    "EditTool",
    "Example",
    "FetchCache",
    "GlobTool",
    "GrepTool",
    "LsTool",
    "MultiEditTool",
    "PersistentShell",
    "ReadTool",
    "ShellSession",
    "SubagentType",
    "TaskTool",
    "TodoWriteTool",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "WebFetchTool",
    "WebSearchTool",
    "WriteTool",
    "apply_edit",
    "build_registry",
    "check_command",
    "clamp_output",
    "file_tools",
    "html_to_markdown",
    "number_lines",
    "parse_todos",
    "render_todos",
    "search_tools",
    "shell_tools",
]
