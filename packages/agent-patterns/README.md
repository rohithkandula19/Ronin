# @ronin/agent-patterns

Production-grade agent patterns for Claude. Pure Python, Pydantic-typed state, opinionated defaults.

## Patterns

| Pattern | Use when |
|---|---|
| `ReActAgent` | Single execution thread, tools are mostly reliable, want the simplest pattern that survives prod. |
| `PlannerExecutorAgent` | Multi-step task that benefits from upfront planning, with checkpoint/resume across replans. |
| `SupervisorAgent` | Heterogeneous sub-tasks with different tool sets / personas; need failure isolation. |
| `ReflexionAgent` | Output quality matters more than latency; you can articulate a critic prompt. |

## Install (workspace dev)

```bash
uv sync --all-packages --all-groups
```

## Example

```python
from ronin_agent_patterns import ReActAgent, Tool

search = Tool(
    name="search",
    description="Search the knowledge base.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    handler=lambda query: my_search_fn(query),
)

agent = ReActAgent(
    system="You are a research assistant. Use search, then synthesize.",
    tools=[search],
    max_iterations=5,
)
result = agent.run("What's the capital of France?")
print(result.output)
for step in result.trace:
    print(f"[{step.kind}] {step.content}")
```

## Tests

```bash
uv run --frozen pytest packages/agent-patterns -q
```

Tests mock the Anthropic client — no API key needed for the test suite. The example app needs `ANTHROPIC_API_KEY`.

## Trace contract

Every pattern returns an `AgentResult` with a typed `trace: list[Step]`. Step kinds:
`thought`, `tool_call`, `tool_result`, `reflection`, `plan`, `final`, `error`.

Pipe the trace into Langfuse, store it in your DB, or render it in your demo UI.
