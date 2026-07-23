# Examples

End-to-end agents built on Ronin. Each example wires multiple modules together (agent-patterns + memory + hardening + mcp-servers + eval-suite) to show what a real product looks like.

| Example | What it demonstrates | Key modules |
|---|---|---|
| [`research-agent/`](research-agent/) | ReAct pattern with a toy KB search tool. Smallest, simplest. | agent-patterns |
| [`customer-support/`](customer-support/) | Multi-Agent Supervisor with triage / billing-lookup / KB-lookup / eng-lookup specialists, Pydantic-validated `DraftReply`, 25-case golden dataset. | agent-patterns, hardening, mcp-servers, eval-suite |
| [`code-reviewer/`](code-reviewer/) | Multi-Agent Supervisor with style / bugs / security specialists, Pydantic-typed `CodeReview` output. Sample buggy file included. | agent-patterns |

## How to run any example

Each example has its own README with a copy-paste run command. The pattern is:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv sync --all-packages --all-groups        # one-time
uv run python examples/<name>/main.py "<input>"
```

Examples need a real LLM key (Anthropic by default — swap `AnthropicProvider` for `OllamaProvider` to run locally with no key).

## Eval

The customer-support example ships a golden dataset. Score it with the eval CLI:

```bash
uv run ronin-eval run examples/customer-support/golden.jsonl \
    --target claude-sonnet-4-6 --judge claude-opus-4-7 \
    --out report.html
```

## Building your own

Start from `research-agent/main.py` (~50 lines) and grow from there. The composition pattern is consistent across all three examples:

```python
provider = AnthropicProvider()                # or OllamaProvider, OpenAICompatProvider, ...
scan = InjectionScanner().scan(user_input)    # hardening at the boundary
if scan.flagged: raise BadInput(...)
agent = ReActAgent(system=..., tools=[...], provider=provider)
result = agent.run(user_input)                # typed AgentResult with full trace
```

Add memory, evals, and approval gates as the use case demands.
