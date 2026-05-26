from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from .base import execute_tool_call
from .providers import AnthropicProvider, LLMProvider, Message
from .types import AgentResult, Step, Tool

# Live-narration hook: called for every step as it's appended to the trace.
OnStep = Callable[[Step], None]
# Pre-tool approval hook: (tool_name, arguments) -> bool. Return False to deny.
BeforeTool = Callable[[str, dict], bool]
# Token-streaming hook: called with each text delta as the model generates it.
OnText = Callable[[str], None]


class ReActAgent(BaseModel):
    """ReAct agent with reflection text, tool error tolerance, and an iteration cap.

    Provider-agnostic: works with any ``LLMProvider`` (Anthropic Claude, Ollama,
    OpenAI, Together, Groq, etc.). Defaults to ``AnthropicProvider``.

    Pick this when:
    - The task fits a single execution thread (no parallel sub-agents needed).
    - Tools are reliable enough that one retry on failure is sufficient.
    - You want the simplest pattern that still survives prod.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    system: str
    tools: list[Tool] = Field(default_factory=list)
    provider: LLMProvider | None = None
    max_iterations: int = 10
    max_tokens: int = 4096

    # Context compaction: when the running message history exceeds this estimated
    # token budget, older tool-result payloads are truncated in place so long
    # sessions don't blow the context window. None disables it.
    compact_after_tokens: int | None = None
    compact_keep_recent: int = 6

    # Backward-compat shortcuts (used only if provider is not supplied):
    model: str | None = None
    api_key: str | None = None

    def model_post_init(self, _ctx: object) -> None:
        if self.provider is None:
            self.provider = AnthropicProvider(
                model=self.model or "claude-sonnet-4-6",
                api_key=self.api_key,
            )

    def run(
        self,
        user_message: str,
        *,
        on_step: OnStep | None = None,
        before_tool: BeforeTool | None = None,
        on_text: OnText | None = None,
    ) -> AgentResult:
        """Run the agent.

        ``on_step``: optional callback fired for every Step as it happens — use
        it to narrate the agent's work live (the basis of ``csk agent``).
        ``before_tool``: optional gate called with (tool_name, arguments) before
        each tool runs. Return False to deny — the agent sees a "denied by user"
        result and reasons around it (human-in-the-loop, Cline-style).
        ``on_text``: optional hook fired with each text delta as the model
        generates it. When supplied, the provider is driven in streaming mode so
        the answer appears token-by-token (the Claude-Code feel).
        """
        assert self.provider is not None  # set by model_post_init
        tools_by_name = {t.name: t for t in self.tools}

        def emit(step: Step) -> None:
            trace.append(step)
            if on_step is not None:
                on_step(step)

        messages: list[Message] = [Message(role="user", content=user_message)]
        trace: list[Step] = []
        usage = {"input_tokens": 0, "output_tokens": 0}

        for i in range(self.max_iterations):
            if self.compact_after_tokens is not None:
                self._maybe_compact(messages, emit)
            if on_text is not None:
                # Streaming mode: forward text deltas live, then take the final
                # assembled response from the terminal ``done`` event.
                response = None
                for ev in self.provider.stream(
                    system=self.system,
                    messages=messages,
                    tools=self.tools,
                    max_tokens=self.max_tokens,
                ):
                    if ev.type == "text" and ev.text:
                        on_text(ev.text)
                    elif ev.type == "done":
                        response = ev.response
                assert response is not None, "stream ended without a 'done' event"
            else:
                response = self.provider.complete(
                    system=self.system,
                    messages=messages,
                    tools=self.tools,
                    max_tokens=self.max_tokens,
                )
            usage["input_tokens"] += response.usage.get("input_tokens", 0)
            usage["output_tokens"] += response.usage.get("output_tokens", 0)

            if response.text:
                emit(Step(kind="thought", content=response.text))

            if not response.tool_calls:
                final = response.text or "(no output)"
                emit(Step(kind="final", content=final))
                return AgentResult(
                    success=True,
                    output=final,
                    iterations=i + 1,
                    trace=trace,
                    usage=usage,
                )

            messages.append(Message(
                role="assistant",
                content=response.text,
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls:
                emit(Step(kind="tool_call", content={"name": tc.name, "input": tc.arguments}))
                tool = tools_by_name.get(tc.name)
                if tool is None:
                    err = f"tool '{tc.name}' is not registered"
                    emit(Step(kind="error", content=err))
                    messages.append(Message(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=f"ERROR: {err}",
                        is_error=True,
                    ))
                    continue

                # Human-in-the-loop gate. If denied, the model sees a denial
                # result and reasons around it (e.g. picks a different tool).
                if before_tool is not None and not before_tool(tc.name, tc.arguments or {}):
                    emit(Step(kind="error", content=f"tool '{tc.name}' denied by user"))
                    messages.append(Message(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content="DENIED: the user declined this action. Do not retry it; find another way or stop.",
                        is_error=True,
                    ))
                    continue

                result, is_err = execute_tool_call(tool, tc.arguments)
                emit(Step(
                    kind="tool_result",
                    content={"name": tc.name, "result": result, "is_error": is_err},
                ))
                messages.append(Message(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=result,
                    is_error=is_err,
                ))

        return AgentResult(
            success=False,
            output="(iteration cap reached)",
            iterations=self.max_iterations,
            trace=trace,
            error=f"hit max_iterations={self.max_iterations}",
            usage=usage,
        )

    def _maybe_compact(self, messages: list[Message], emit) -> None:
        """Shrink old tool-result payloads when the history grows too large.

        Truncates the *content* of old ``tool`` messages in place rather than
        removing messages — this preserves the assistant↔tool_call_id pairing
        the API requires (no orphaned tool results), while reclaiming the bulk
        of the tokens (file dumps, command output). The last
        ``compact_keep_recent`` messages are left untouched so the model keeps
        full recent context.
        """
        threshold = self.compact_after_tokens
        if threshold is None:
            return

        def est_tokens() -> int:
            return sum(len(m.content or "") for m in messages) // 4

        if est_tokens() <= threshold:
            return

        marker = " …[truncated by context compaction]"
        compacted = 0
        for m in messages[: -self.compact_keep_recent] if self.compact_keep_recent else messages:
            if m.role == "tool" and m.content and len(m.content) > 240 and marker not in m.content:
                m.content = m.content[:200] + marker
                compacted += 1
        if compacted:
            emit(Step(kind="thought", content=f"[context compacted: truncated {compacted} old tool result(s)]"))
