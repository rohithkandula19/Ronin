"""Web research for the agent — ``ronin research "<question>"``.

The agent already has free, keyless web tools (``web_search`` + ``fetch_url`` in
``web_tools.py``). This module makes web research a first-class command: it runs
the read-only code agent with a research-shaped system prompt so the model
searches the web, fetches the most relevant pages, and answers the question with
sources.

The actual agent run is INJECTED (``runner``) so this module is testable fully
offline: a test passes a stub runner and asserts the question reached it and the
answer came back. The default runner is the same ``run_code_agent`` path the rest
of the CLI uses, imported lazily so importing this module stays light.

Nothing here sleeps or dials out on its own. If the agent stack is unavailable
(no model, no key), the command degrades to a raw web search so the user still
gets links rather than an error.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# The system nudge that turns a generic agent run into a research run. Plain and
# explicit: search first, read the best sources, answer with a short cited list.
RESEARCH_SYSTEM = (
    "You are answering a research question for the owner. Use web_search to find "
    "relevant pages, then fetch_url to read the most promising ones before you "
    "answer. Do not guess. Base your answer on what you actually read. Finish "
    "with a short, plain answer followed by the source URLs you used. If the web "
    "is unreachable, say so plainly."
)

# runner(question) -> str. Returns the agent's answer text.
Runner = Callable[[str], str]


def _default_runner(config: Any, root: Any) -> Runner:
    """Build the real runner: the read-only code agent with web tools.

    Imported lazily so importing this module needs neither a model nor the heavy
    agent stack. The agent already gets ``web_search`` + ``fetch_url`` in its tool
    list (see ``code_mode.run_code_agent``), so we only steer it with a research
    system prompt and run it read-only.
    """
    from .code_mode import run_code_agent

    def run(question: str) -> str:
        res = run_code_agent(
            config,
            question,
            root=root,
            console=None,
            yolo=True,
            read_only=True,
            include_image_tool=False,
            base_system=RESEARCH_SYSTEM,
        )
        return res.output or res.error or "(no answer)"

    return run


def _fallback_search(question: str) -> str:
    """Last resort when the agent stack is unavailable: a raw web search.

    Returns the search results (title / url / snippet) so the user still gets
    links to read. Imported lazily and guarded so a missing web stack degrades to
    a plain message instead of raising.
    """
    try:
        from .web_tools import web_search
    except Exception:  # noqa: BLE001
        return "research is unavailable on this host (no web tools)."
    return web_search(question)


def run_research(
    question: str,
    *,
    runner: Optional[Runner] = None,
    config: Any = None,
    root: Any = ".",
) -> str:
    """Answer ``question`` from the web and return the answer text.

    ``runner`` is injected for tests (a stub ``(question) -> str``). When it is
    None the default agent runner is built from ``config`` + ``root``. If building
    or running the agent fails for any reason, we degrade to a raw web search so
    the user still gets links rather than a traceback.
    """
    question = (question or "").strip()
    if not question:
        return "ask a question, e.g. ronin research \"who won the 2022 world cup\"."

    if runner is None:
        try:
            runner = _default_runner(config, root)
        except Exception:  # noqa: BLE001 - degrade to a raw search
            return _fallback_search(question)

    try:
        answer = runner(question)
    except Exception:  # noqa: BLE001 - degrade to a raw search
        return _fallback_search(question)

    return (answer or "").strip() or _fallback_search(question)
