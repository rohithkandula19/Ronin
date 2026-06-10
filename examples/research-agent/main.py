"""Research agent example using the ReAct pattern.

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    uv run python examples/research-agent/main.py "What is the ReAct pattern?"
"""
from __future__ import annotations

import sys

from ronin_agent_patterns import ReActAgent, Tool

KB = {
    "claude": "Claude is a family of LLMs by Anthropic.",
    "anthropic": "Anthropic is an AI safety company founded in 2021.",
    "react pattern": (
        "ReAct combines reasoning and acting: the LLM emits a thought, calls a tool, "
        "observes the result, and loops until the task is done."
    ),
    "reflexion": "Reflexion adds a critic LLM that evaluates output and triggers retries with feedback.",
}


def search(query: str) -> str:
    q = query.lower()
    hits = [v for k, v in KB.items() if k in q or q in k]
    return "\n".join(hits) if hits else "no results"


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: python main.py "your question"')
        sys.exit(1)

    question = " ".join(sys.argv[1:])

    search_tool = Tool(
        name="search",
        description="Search the knowledge base for a query string.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
        handler=search,
    )

    agent = ReActAgent(
        system=(
            "You are a research assistant. Use the search tool to find information, "
            "then synthesize a clear answer. Cite what you found."
        ),
        tools=[search_tool],
        max_iterations=5,
    )

    result = agent.run(question)

    print("=" * 60)
    print(f"SUCCESS: {result.success}")
    print(f"ITERATIONS: {result.iterations}")
    print(f"USAGE: {result.usage}")
    print(f"\nANSWER:\n{result.output}")
    print("\nTRACE:")
    for step in result.trace:
        print(f"  [{step.kind}] {str(step.content)[:120]}")


if __name__ == "__main__":
    main()
