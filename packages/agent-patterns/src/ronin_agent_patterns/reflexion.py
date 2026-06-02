from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .providers import AnthropicProvider, LLMProvider, Message
from .react import ReActAgent
from .types import AgentResult, Step, Tool


class Critique(BaseModel):
    is_acceptable: bool
    feedback: str


class ReflexionAgent(BaseModel):
    """Act → reflect → retry-with-self-critique."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent_system: str
    critic_system: str
    tools: list[Tool] = Field(default_factory=list)
    provider: LLMProvider | None = None
    max_attempts: int = 3
    max_iterations_per_attempt: int = 10

    model: str | None = None
    api_key: str | None = None

    def model_post_init(self, _ctx: object) -> None:
        if self.provider is None:
            self.provider = AnthropicProvider(
                model=self.model or "claude-sonnet-4-6",
                api_key=self.api_key,
            )

    def _critique(self, task: str, output: str) -> Critique:
        assert self.provider is not None
        prompt = (
            f"Original task:\n{task}\n\n"
            f"Agent's output:\n{output}\n\n"
            "Evaluate whether this output adequately solves the task. Respond exactly:\n"
            "ACCEPTABLE: <yes|no>\n"
            "FEEDBACK: <one paragraph of specific, actionable feedback>"
        )
        response = self.provider.complete(
            system=self.critic_system,
            messages=[Message(role="user", content=prompt)],
            tools=[],
            max_tokens=1024,
        )

        is_acceptable = False
        feedback_lines: list[str] = []
        in_feedback = False
        for line in response.text.splitlines():
            stripped = line.strip()
            lowered = stripped.lower()
            if lowered.startswith("acceptable:"):
                is_acceptable = "yes" in lowered
                in_feedback = False
            elif lowered.startswith("feedback:"):
                feedback_lines.append(stripped.split(":", 1)[1].strip())
                in_feedback = True
            elif in_feedback and stripped:
                feedback_lines.append(stripped)

        feedback = " ".join(feedback_lines).strip() or response.text
        return Critique(is_acceptable=is_acceptable, feedback=feedback)

    def run(self, task: str) -> AgentResult:
        trace: list[Step] = []
        last_result: AgentResult | None = None
        feedback_so_far = ""

        for attempt in range(self.max_attempts):
            agent_input = task if not feedback_so_far else (
                f"{task}\n\nPrior attempt feedback:\n{feedback_so_far}"
            )
            agent = ReActAgent(
                system=self.agent_system,
                tools=self.tools,
                provider=self.provider,
                max_iterations=self.max_iterations_per_attempt,
            )
            result = agent.run(agent_input)
            trace.append(Step(kind="thought", content=f"attempt {attempt + 1}"))
            trace.extend(result.trace)
            last_result = result

            if not result.success:
                feedback_so_far = f"Previous attempt errored: {result.error}"
                continue

            critique = self._critique(task, result.output)
            trace.append(Step(kind="reflection", content=critique.model_dump()))
            if critique.is_acceptable:
                return AgentResult(
                    success=True,
                    output=result.output,
                    iterations=attempt + 1,
                    trace=trace,
                    usage=result.usage,
                )
            feedback_so_far = critique.feedback

        return AgentResult(
            success=False,
            output=last_result.output if last_result else "",
            iterations=self.max_attempts,
            trace=trace,
            error=f"max_attempts={self.max_attempts} reached without acceptable output",
            usage=last_result.usage if last_result else {},
        )
