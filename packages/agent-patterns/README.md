# @ronin/agent-patterns

Production-grade agent patterns for Claude. Pure Python, Pydantic-typed state, opinionated defaults.

## Patterns

| Pattern | Use when |
|---|---|
| `ReActAgent` | Single execution thread, tools are mostly reliable, want the simplest pattern that survives prod. |
| `PlannerExecutorAgent` | Multi-step task that benefits from upfront planning, with checkpoint/resume across replans. |
| `SupervisorAgent` | One orchestrator delegates dynamically to named sub-agents via tools; heterogeneous personas, failure isolation. |
| `OrchestratorAgent` | Decompose a goal into assigned subtasks and run provider-agnostic sub-agents (each on its own model, parallel where independent), then synthesize. |
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

## Run Budgets

Use optional per-run ceilings to bound a loop without treating an incomplete
answer as successful:

```python
agent = ReActAgent(
    system="Make a small, verified change.",
    max_total_tokens=12_000,
    max_wall_time_seconds=300,
    max_cost_usd=0.50,
)
```

Budgets are checked before each new provider request and after each response.
Usage arrives after a request completes, so one request can exceed its ceiling;
Ronin stops before executing any newly requested tool or making another provider
call, returns the partial text, and records an `error` step with
`budget_exhausted=True`. `max_cost_usd` only acts when the provider explicitly
reports `usage["cost_usd"]`; Ronin does not estimate or invent a price.

## Tests

```bash
uv run --frozen pytest packages/agent-patterns -q
```

Tests mock the Anthropic client — no API key needed for the test suite. The example app needs `ANTHROPIC_API_KEY`.

## Trace contract

Every pattern returns an `AgentResult` with a typed `trace: list[Step]`. Step kinds:
`thought`, `tool_call`, `tool_result`, `reflection`, `plan`, `final`, `error`.

Pipe the trace into Langfuse, store it in your DB, or render it in your demo UI.
