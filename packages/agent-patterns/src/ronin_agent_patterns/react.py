from __future__ import annotations

import json
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from .base import execute_tool_call
from .providers import AnthropicProvider, LLMProvider, Message
from .types import AgentResult, Step, Tool

# Live-narration hook: called for every step as it's appended to the trace.
OnStep = Callable[[Step], None]
# Pre-tool approval hook: (tool_name, arguments) -> bool | str.
#   True  → allow.  False → deny (generic).  A non-empty str → deny *with that
#   reason*, which is handed back to the model so it can adjust (reject-with-feedback).
BeforeTool = Callable[[str, dict], "bool | str"]
# Token-streaming hook: called with each text delta as the model generates it.
OnText = Callable[[str], None]
# Stream-reset hook: called when a retry is about to re-stream the answer from
# the start (e.g. after a mid-stream rate-limit). The consumer must discard the
# partial text it has shown so far, so the answer renders exactly once.
OnReset = Callable[[], None]
# Post-tool hook: (tool_name, arguments, result, is_error) -> None. Fires after
# each tool runs — used for side-effects like auto-format/test hooks.
AfterTool = Callable[[str, dict, str, bool], None]


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
    # Hard per-result cap: no single tool result may exceed this many characters
    # when it enters the context (a runaway `list_files`/`search` can't blow the
    # window in one shot). 0 disables.
    max_tool_result_chars: int = 16000

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
        history: "list[Message] | None" = None,
        on_step: OnStep | None = None,
        before_tool: BeforeTool | None = None,
        on_text: OnText | None = None,
        on_reset: OnReset | None = None,
        after_tool: "AfterTool | None" = None,
        parallel_safe: "Callable[[str], bool] | None" = None,
    ) -> AgentResult:
        """Run the agent.

        ``history``: optional prior conversation (a list of ``Message``) to seed
        this run with. When supplied, the agent keeps real cross-turn context —
        files it read, tool results, prior answers — instead of starting cold.
        The full updated list is returned as ``AgentResult.messages`` so the
        caller can feed it back next turn. Also keeps the provider's prompt-cache
        prefix stable across turns (a big latency/cost win on Anthropic).
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

        # Seed with any prior conversation, then append this turn's user message.
        messages: list[Message] = list(history) if history else []
        messages.append(Message(role="user", content=user_message))
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
                    elif ev.type == "reset":
                        # A retry is re-streaming from scratch; drop the partial
                        # we already forwarded so the answer isn't shown twice.
                        if on_reset is not None:
                            on_reset()
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
            # Carry prompt-cache counts when the provider reports them, so callers
            # can show cache savings. Absent (e.g. non-Anthropic) → keys never appear.
            for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
                if k in response.usage:
                    usage[k] = usage.get(k, 0) + response.usage[k]

            if response.text:
                emit(Step(kind="thought", content=response.text))

            if not response.tool_calls:
                final = response.text or "(no output)"
                emit(Step(kind="final", content=final))
                # Keep the final answer in the history so the next turn (when the
                # caller feeds ``messages`` back as ``history``) sees what we said.
                messages.append(Message(role="assistant", content=final))
                return AgentResult(
                    success=True,
                    output=final,
                    iterations=i + 1,
                    trace=trace,
                    usage=usage,
                    messages=messages,
                )

            messages.append(Message(
                role="assistant",
                content=response.text,
                tool_calls=response.tool_calls,
            ))

            calls = list(response.tool_calls)
            # Parallelise a batch only when EVERY call is declared safe (read-only
            # / idempotent) by the caller — never writes, commands, or anything
            # gated. Order of results is preserved to match the tool_call ids.
            if (len(calls) > 1 and parallel_safe is not None
                    and all(parallel_safe(tc.name) for tc in calls)):
                self._run_parallel(calls, tools_by_name, before_tool, after_tool, emit, messages)
            else:
                for tc in calls:
                    self._run_one(tc, tools_by_name, before_tool, after_tool, emit, messages)

        return AgentResult(
            success=False,
            output="(iteration cap reached)",
            iterations=self.max_iterations,
            trace=trace,
            error=f"hit max_iterations={self.max_iterations}",
            usage=usage,
            messages=messages,
        )

    def _resolve_and_gate(self, tc, tools_by_name, before_tool, emit):
        """Announce the call, resolve the tool, run the gate. Returns
        (tool, denied_or_missing_message|None). A non-None message means don't
        execute — append it as the tool result."""
        emit(Step(kind="tool_call", content={"name": tc.name, "input": tc.arguments}))
        tool = tools_by_name.get(tc.name)
        if tool is None:
            err = f"tool '{tc.name}' is not registered"
            emit(Step(kind="error", content=err))
            return None, f"ERROR: {err}"
        if before_tool is not None:
            verdict = before_tool(tc.name, tc.arguments or {})
            # Contract: ANY str → a denial (non-empty = reject-with-feedback with
            # that reason; blank = generic deny — a string never means allow);
            # False/None/0 → generic deny; any other truthy (True or a legacy
            # sentinel) → allow.
            if isinstance(verdict, str):
                reason = verdict.strip()
                if reason:
                    emit(Step(kind="error", content=f"tool '{tc.name}' denied: {reason}"))
                    return None, (f"DENIED by the user, who said: \"{reason}\". Adjust your "
                                  "approach based on this feedback; do not retry the same call.")
                emit(Step(kind="error", content=f"tool '{tc.name}' denied by user"))
                return None, ("DENIED: the user declined this action. Do not retry it; "
                              "find another way or stop.")
            if not verdict:
                emit(Step(kind="error", content=f"tool '{tc.name}' denied by user"))
                return None, ("DENIED: the user declined this action. Do not retry it; "
                              "find another way or stop.")
        return tool, None

    def _record(self, tc, result, is_err, after_tool, emit, messages):
        result = self._cap_result(result)
        if after_tool is not None:
            try:
                after_tool(tc.name, tc.arguments or {}, result, is_err)
            except Exception:  # noqa: BLE001 — a hook must never break the run
                pass
        emit(Step(kind="tool_result", content={"name": tc.name, "result": result, "is_error": is_err}))
        messages.append(Message(role="tool", tool_call_id=tc.id, name=tc.name,
                                content=result, is_error=is_err))

    def _run_one(self, tc, tools_by_name, before_tool, after_tool, emit, messages):
        """Execute a single tool call (announce → gate → run → record)."""
        tool, blocked = self._resolve_and_gate(tc, tools_by_name, before_tool, emit)
        if blocked is not None:
            messages.append(Message(role="tool", tool_call_id=tc.id, name=tc.name,
                                    content=blocked, is_error=True))
            return
        result, is_err = execute_tool_call(tool, tc.arguments)
        self._record(tc, result, is_err, after_tool, emit, messages)

    def _run_parallel(self, calls, tools_by_name, before_tool, after_tool, emit, messages):
        """Run a batch of read-only/idempotent tool calls concurrently. Tool calls
        are announced and gated up front (gates are non-blocking for these), the
        handlers run on a thread pool, and results are appended in original order
        so the assistant↔tool_call_id pairing the API needs is preserved."""
        from concurrent.futures import ThreadPoolExecutor

        plan = [(tc, *self._resolve_and_gate(tc, tools_by_name, before_tool, emit)) for tc in calls]
        runnable = [(i, tc, tool) for i, (tc, tool, blocked) in enumerate(plan) if blocked is None]
        outcomes: dict[int, tuple] = {}
        if runnable:
            with ThreadPoolExecutor(max_workers=min(8, len(runnable))) as ex:
                fut_to_i = {ex.submit(execute_tool_call, tool, tc.arguments): i
                            for i, tc, tool in runnable}
                for fut in fut_to_i:
                    i = fut_to_i[fut]
                    try:
                        outcomes[i] = fut.result()
                    except Exception as e:  # noqa: BLE001
                        outcomes[i] = (f"ERROR: {e}", True)
        for i, (tc, tool, blocked) in enumerate(plan):
            if blocked is not None:
                messages.append(Message(role="tool", tool_call_id=tc.id, name=tc.name,
                                        content=blocked, is_error=True))
            else:
                result, is_err = outcomes[i]
                self._record(tc, result, is_err, after_tool, emit, messages)

    def _cap_result(self, result: str) -> str:
        """Truncate a single tool result that's too large to enter context whole.
        Keeps the head (where lists/search hits/file starts carry the signal) and
        tells the model how to get the rest."""
        cap = self.max_tool_result_chars
        if not cap or not result or len(result) <= cap:
            return result
        return (result[:cap] + f"\n…[tool result truncated: showed {cap:,} of "
                f"{len(result):,} chars. Narrow the query or read a specific "
                "file/section to see more.]")

    def _maybe_compact(self, messages: list[Message], emit) -> None:
        """Keep the running history under the context budget.

        Two stages, both pairing-safe:

        1. **Truncate** the *content* of old ``tool`` messages in place (file
           dumps, command output) — reclaims the bulk of the tokens without
           removing any message, so assistant↔tool_call_id pairing is intact.
        2. **Evict** whole oldest turn-groups when truncation isn't enough.
           Persisted cross-turn history accumulates user/assistant text and
           tool-call *arguments* that tool-truncation can't reclaim; without a
           cap a very long session would grow past the window and wedge (every
           later turn re-sends the oversized history and re-fails). A group
           starts at a ``user`` message, so dropping from the front at a user
           boundary never orphans a ``tool`` result. The most recent group (the
           current turn) is always kept.

        The last ``compact_keep_recent`` messages are left untouched in stage 1
        so the model keeps full recent context.
        """
        threshold = self.compact_after_tokens
        if threshold is None:
            return

        def est_tokens() -> int:
            # Count tool-call ARGUMENTS too — write_file/edit_file payloads live
            # in m.tool_calls[].arguments, not m.content, so counting only content
            # would wildly undercount a persisted, edit-heavy history.
            total = 0
            for m in messages:
                total += len(m.content or "")
                for tc in (m.tool_calls or []):
                    total += len(json.dumps(tc.arguments, default=str))
            return total // 4

        if est_tokens() <= threshold:
            return

        marker = " …[truncated by context compaction]"
        compacted = 0
        for m in messages[: -self.compact_keep_recent] if self.compact_keep_recent else messages:
            if m.role == "tool" and m.content and len(m.content) > 240 and marker not in m.content:
                m.content = m.content[:200] + marker
                compacted += 1

        # Stage 2: still over budget after truncating tool output → evict oldest
        # complete turn-groups (everything before the 2nd user message) until we
        # fit or only the current turn remains.
        evicted = 0
        while est_tokens() > threshold:
            user_idx = [i for i, m in enumerate(messages) if m.role == "user"]
            if len(user_idx) <= 1:
                break  # never drop the most recent turn-group
            del messages[: user_idx[1]]
            evicted += 1

        if compacted or evicted:
            note = []
            if compacted:
                note.append(f"truncated {compacted} old tool result(s)")
            if evicted:
                note.append(f"dropped {evicted} oldest turn(s)")
            emit(Step(kind="thought", content=f"[context compacted: {', '.join(note)}]"))
