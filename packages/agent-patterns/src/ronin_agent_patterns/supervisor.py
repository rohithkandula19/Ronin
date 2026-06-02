from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .providers import AnthropicProvider, LLMProvider
from .react import ReActAgent
from .types import AgentResult, Step, Tool


class SubAgent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    system: str
    tools: list[Tool] = Field(default_factory=list)
    provider: LLMProvider | None = None
    max_iterations: int = 10


class SupervisorAgent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    system: str
    sub_agents: list[SubAgent]
    provider: LLMProvider | None = None
    max_iterations: int = 10

    # Backward-compat:
    model: str | None = None
    api_key: str | None = None

    def model_post_init(self, _ctx: object) -> None:
        if self.provider is None:
            self.provider = AnthropicProvider(
                model=self.model or "claude-sonnet-4-6",
                api_key=self.api_key,
            )

    def _delegate_tool(self, sub: SubAgent, parent_trace: list[Step]) -> Tool:
        sub_provider = sub.provider or self.provider

        def handler(query: str) -> str:
            agent = ReActAgent(
                system=sub.system,
                tools=sub.tools,
                provider=sub_provider,
                max_iterations=sub.max_iterations,
            )
            try:
                result = agent.run(query)
            except Exception as exc:  # noqa: BLE001
                parent_trace.append(
                    Step(kind="error", content=f"sub-agent {sub.name} crashed: {exc}")
                )
                return f"SUB_AGENT_ERROR: {exc}"
            parent_trace.append(Step(
                kind="tool_result",
                content={
                    "sub_agent": sub.name,
                    "success": result.success,
                    "output": result.output,
                },
                metadata={"sub_iterations": result.iterations},
            ))
            if not result.success:
                return f"SUB_AGENT_FAILED: {result.error}\nPartial output: {result.output}"
            return result.output

        return Tool(
            name=f"delegate_to_{sub.name}",
            description=f"Delegate a task to {sub.name}. {sub.description}",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The task or question for the sub-agent.",
                    },
                },
                "required": ["query"],
            },
            handler=handler,
        )

    def run(self, task: str) -> AgentResult:
        parent_trace: list[Step] = []
        delegate_tools = [self._delegate_tool(sub, parent_trace) for sub in self.sub_agents]

        supervisor_system = (
            f"{self.system}\n\n"
            "You orchestrate specialist sub-agents. Delegate via the delegate_to_<name> tools, "
            "then synthesize their outputs into a final answer."
        )
        orchestrator = ReActAgent(
            system=supervisor_system,
            tools=delegate_tools,
            provider=self.provider,
            max_iterations=self.max_iterations,
        )
        result = orchestrator.run(task)
        return AgentResult(
            success=result.success,
            output=result.output,
            iterations=result.iterations,
            trace=parent_trace + result.trace,
            error=result.error,
            usage=result.usage,
        )
