from __future__ import annotations

import json
from time import monotonic
from typing import Any, Callable, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .base import execute_tool_call
from .contracts import AgentRequest, ContextProvider, assemble_context
from .durable import BudgetExceeded, RunBudget, RunJournal
from .providers import AnthropicProvider, LLMProvider, Message, ToolCall
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


def trim_to_complete_pairs(messages: list) -> list:
    """Drop a dangling final tool-call round so history stays API-valid.

    An assistant message carrying ``tool_calls`` must be followed by a ``tool``
    message for every call id. When a turn is adopted after the iteration cap (or
    an interrupt), the tail can be an assistant that requested calls whose results
    were never appended — replaying that as history would orphan a ``tool_use``.
    Truncate to just before that incomplete assistant message; everything earlier
    (all completed rounds, every file already read) is preserved.
    """
    last = None
    for idx in range(len(messages) - 1, -1, -1):
        m = messages[idx]
        if getattr(m, "role", None) == "assistant" and getattr(m, "tool_calls", None):
            last = idx
            break
    if last is None:
        return messages
    needed = {tc.id for tc in messages[last].tool_calls}
    have = {getattr(m, "tool_call_id", None) for m in messages[last + 1:]}
    return messages if needed.issubset(have) else messages[:last]


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

    # Optional ceilings for this individual run. They are evaluated before each
    # provider request and after a provider reports usage. A provider can only
    # report usage after a request finishes, so a single request may overshoot;
    # the agent then stops before it runs tools or makes another provider call.
    max_total_tokens: int | None = Field(default=None, gt=0)
    max_wall_time_seconds: float | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, gt=0)

    # Context compaction: when the running message history exceeds this estimated
    # token budget, older tool-result payloads are truncated in place so long
    # sessions don't blow the context window. None disables it.
    compact_after_tokens: int | None = None
    compact_keep_recent: int = 6
    # Hard per-result cap: no single tool result may exceed this many characters
    # when it enters the context (a runaway `list_files`/`search` can't blow the
    # window in one shot). 0 disables.
    max_tool_result_chars: int = 16000
    # Typed, local context providers are resolved once per fresh run. Their
    # rendered prompt is checkpointed so recovery never changes task context.
    context_providers: list[Any] = Field(default_factory=list)
    max_runtime_context_chars: int = 16_000

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
        journal: RunJournal | None = None,
        journal_run_id: str | None = None,
        budget: RunBudget | None = None,
        resume_state: dict[str, Any] | None = None,
        runtime_context: dict[str, Any] | None = None,
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
        ``journal``: optional local SQLite run journal. It checkpoints before
        provider and tool actions, records safe lifecycle events, and leaves an
        interrupted run resumable after a process exit.
        ``budget``: optional hard cross-action budget. It is checked before each
        provider request and tool invocation, with reservations for concurrency.
        ``resume_state``: state returned by ``journal.resume(run_id)``. Use
        :meth:`resume` rather than constructing it manually.
        ``runtime_context``: typed metadata supplied to this run's context
        providers. Providers resolve once before the first model request.
        """
        assert self.provider is not None  # set by model_post_init
        tools_by_name = {t.name: t for t in self.tools}

        def emit(step: Step) -> None:
            trace.append(step)
            if on_step is not None:
                # A buggy or markup-crashing renderer must never abort the run:
                # narration is cosmetic, so swallow its errors and keep going.
                try:
                    on_step(step)
                except Exception:
                    pass

        # Seed with any prior conversation, then append this turn's user message.
        # A resume starts exactly from a prior checkpoint instead.
        if resume_state is None:
            messages: list[Message] = list(history) if history else []
            messages.append(Message(role="user", content=user_message))
            trace: list[Step] = []
            usage: dict[str, int | float] = {"input_tokens": 0, "output_tokens": 0}
            start_iteration = 0
            pending_calls: list[ToolCall] = []
            assembly = assemble_context(
                self.system,
                AgentRequest(task=user_message, metadata=runtime_context or {}),
                self.context_providers,
                max_context_chars=self.max_runtime_context_chars,
            )
            effective_system = assembly.system_prompt
        else:
            messages = [Message.model_validate(item) for item in resume_state.get("messages", [])]
            trace = [Step.model_validate(item) for item in resume_state.get("trace", [])]
            restored_usage = resume_state.get("usage", {})
            if not messages or not isinstance(restored_usage, dict):
                raise ValueError("durable resume state is missing conversation history or usage")
            usage = {str(key): value for key, value in restored_usage.items() if isinstance(value, (int, float))}
            start_iteration = max(0, int(resume_state.get("next_iteration", 0)))
            pending_calls = [
                ToolCall.model_validate(item)
                for item in resume_state.get("pending_tool_calls", [])
            ]
            effective_system = str(resume_state.get("system_prompt", self.system))
            if budget is not None:
                restored_budget = resume_state.get("budget", {})
                if isinstance(restored_budget, dict):
                    budget.restore(restored_budget)
        started_at = monotonic()
        active_run_id = ""

        def state(next_iteration: int, pending: list[ToolCall] | None = None) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "messages": [message.model_dump(mode="json") for message in messages],
                "trace": [step.model_dump(mode="json") for step in trace],
                "usage": usage,
                "next_iteration": next_iteration,
                "pending_tool_calls": [call.model_dump(mode="json") for call in pending or []],
                "system_prompt": effective_system,
                "budget": budget.snapshot() if budget is not None else {},
            }

        if journal is not None:
            if resume_state is None:
                active_run_id = journal.start(state(start_iteration), run_id=journal_run_id)
                journal.append_event(
                    active_run_id,
                    "context_resolved",
                    {
                        "sources": assembly.sources,
                        "truncated_sources": assembly.truncated_sources,
                        "failed_sources": assembly.failed_sources,
                    },
                )
            else:
                if not journal_run_id:
                    raise ValueError("journal_run_id is required when resuming a durable run")
                active_run_id = journal_run_id

        def checkpoint(next_iteration: int, pending: list[ToolCall] | None = None) -> None:
            if journal is not None:
                journal.checkpoint(active_run_id, state(next_iteration, pending))

        def record_budget(action: str, decision) -> None:
            if journal is not None:
                journal.append_event(
                    active_run_id,
                    "budget_checked",
                    {
                        "action": action,
                        "allowed": decision.allowed,
                        "warnings": list(decision.warnings),
                        "reason": decision.reason,
                        "usage": budget.snapshot() if budget is not None else {},
                    },
                )

        def complete(result: AgentResult, status: str) -> AgentResult:
            if journal is not None:
                journal.finish(active_run_id, status=status, error=result.error or "")
            return result

        current_iteration = start_iteration
        try:
            if pending_calls:
                # A checkpoint taken after the provider requested tools contains
                # those calls. Re-run them before another provider request so the
                # transcript remains API-valid and no work is silently skipped.
                for index, tc in enumerate(pending_calls):
                    remaining = pending_calls[index:]
                    later = pending_calls[index + 1:]
                    self._run_one(
                        tc, tools_by_name, before_tool, after_tool, emit, messages,
                        budget=budget, journal=journal, run_id=active_run_id,
                        checkpoint=lambda remaining=remaining: checkpoint(start_iteration, remaining),
                        checkpoint_after=lambda later=later: checkpoint(
                            start_iteration + (1 if not later else 0), later,
                        ),
                        record_budget=record_budget,
                    )
                start_iteration += 1
            for i in range(start_iteration, self.max_iterations):
                current_iteration = i
                budget_error = self._budget_error(usage, started_at)
                if budget_error is not None:
                    return complete(self._budget_result(
                        budget_error, "", i, trace, usage, messages, emit,
                    ), "blocked")
                if budget is not None:
                    decision = budget.before_provider()
                    record_budget("provider", decision)
                    if not decision.allowed:
                        return complete(self._budget_result(
                            decision.reason, "", i, trace, usage, messages, emit,
                        ), "blocked")
                if self.compact_after_tokens is not None:
                    self._maybe_compact(messages, emit)
                checkpoint(i)
                if journal is not None:
                    journal.append_event(active_run_id, "provider_requested", {"iteration": i + 1})
                if on_text is not None:
                    # Streaming mode: forward text deltas live, then take the final
                    # assembled response from the terminal ``done`` event.
                    response = None
                    for ev in self.provider.stream(
                        system=effective_system,
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
                        system=effective_system,
                        messages=messages,
                        tools=self.tools,
                        max_tokens=self.max_tokens,
                    )
                # Preserve every provider-reported usage dimension. In particular,
                # max_cost_usd only acts on an explicit ``cost_usd`` value; it never
                # guesses a price from a model name or token count.
                for key, value in response.usage.items():
                    usage[key] = usage.get(key, 0) + value
                if budget is not None:
                    decision = budget.record_provider_usage(response.usage)
                    record_budget("provider_result", decision)

                if response.text:
                    emit(Step(kind="thought", content=response.text))

                budget_error = self._budget_error(usage, started_at)
                if budget_error is not None:
                    return complete(self._budget_result(
                        budget_error, response.text, i + 1, trace, usage, messages, emit,
                    ), "blocked")
                if budget is not None and not decision.allowed:
                    return complete(self._budget_result(
                        decision.reason, response.text, i + 1, trace, usage, messages, emit,
                    ), "blocked")

                if not response.tool_calls:
                    final = response.text or "(no output)"
                    emit(Step(kind="final", content=final))
                    # Keep the final answer in the history so the next turn (when the
                    # caller feeds ``messages`` back as ``history``) sees what we said.
                    messages.append(Message(role="assistant", content=final))
                    return complete(AgentResult(
                        success=True,
                        output=final,
                        iterations=i + 1,
                        trace=trace,
                        usage=usage,
                        messages=messages,
                    ), "completed")

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
                    checkpoint(i, calls)
                    self._run_parallel(
                        calls, tools_by_name, before_tool, after_tool, emit, messages,
                        budget=budget, journal=journal, run_id=active_run_id,
                        checkpoint=None, record_budget=record_budget,
                    )
                    checkpoint(i + 1)
                else:
                    for index, tc in enumerate(calls):
                        remaining = calls[index:]
                        later = calls[index + 1:]
                        self._run_one(
                            tc, tools_by_name, before_tool, after_tool, emit, messages,
                            budget=budget, journal=journal, run_id=active_run_id,
                            checkpoint=lambda remaining=remaining: checkpoint(i, remaining),
                            checkpoint_after=lambda later=later: checkpoint(i + (1 if not later else 0), later),
                            record_budget=record_budget,
                        )

        except BudgetExceeded as exc:
            return complete(self._budget_result(
                str(exc), "", current_iteration + 1, trace, usage, messages, emit,
            ), "blocked")

        except KeyboardInterrupt:
            # Ctrl-C ends the turn but must not throw away what the agent already
            # did. Preserve the structured history (trimmed to complete pairs) so
            # the next turn keeps every file read; mark it interrupted for context.
            messages.append(Message(role="user", content="(previous turn interrupted by user before completion)"))
            return complete(AgentResult(
                success=False,
                output="(interrupted by user)",
                iterations=i + 1,
                trace=trace,
                error="interrupted by user",
                usage=usage,
                messages=trim_to_complete_pairs(messages),
            ), "interrupted")
        return complete(AgentResult(
            success=False,
            output="(iteration cap reached)",
            iterations=self.max_iterations,
            trace=trace,
            error=f"hit max_iterations={self.max_iterations}",
            usage=usage,
            # Preserve the structured history so the caller can adopt it — the
            # next turn keeps every file read across these iterations instead of
            # starting cold. Trim any dangling final tool-call round first.
            messages=trim_to_complete_pairs(messages),
        ), "failed")

    def resume(
        self,
        run_id: str,
        journal: RunJournal,
        *,
        on_step: OnStep | None = None,
        before_tool: BeforeTool | None = None,
        on_text: OnText | None = None,
        on_reset: OnReset | None = None,
        after_tool: AfterTool | None = None,
        parallel_safe: "Callable[[str], bool] | None" = None,
        budget: RunBudget | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Resume an interrupted durable run from its latest verified checkpoint."""
        return self.run(
            "",
            on_step=on_step,
            before_tool=before_tool,
            on_text=on_text,
            on_reset=on_reset,
            after_tool=after_tool,
            parallel_safe=parallel_safe,
            journal=journal,
            journal_run_id=run_id,
            budget=budget,
            resume_state=journal.resume(run_id),
            runtime_context=runtime_context,
        )

    def _budget_error(self, usage: dict[str, int | float], started_at: float) -> str | None:
        """Return an honest limit message when this run cannot continue."""
        if self.max_total_tokens is not None:
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            if tokens >= self.max_total_tokens:
                return ("run token budget reached: "
                        f"{tokens:,.0f}/{self.max_total_tokens:,} reported input + output tokens")
        if self.max_wall_time_seconds is not None:
            elapsed = monotonic() - started_at
            if elapsed >= self.max_wall_time_seconds:
                return ("run wall-clock budget reached: "
                        f"{elapsed:.2f}s/{self.max_wall_time_seconds:.2f}s")
        if self.max_cost_usd is not None:
            cost = usage.get("cost_usd", 0)
            if cost >= self.max_cost_usd:
                return ("run reported-cost budget reached: "
                        f"${cost:.6f}/${self.max_cost_usd:.6f}")
        return None

    def _budget_result(
        self,
        error: str,
        partial_output: str,
        iterations: int,
        trace: list[Step],
        usage: dict[str, int | float],
        messages: list[Message],
        emit: OnStep,
    ) -> AgentResult:
        """Stop without executing a newly requested tool or fabricating success."""
        emit(Step(kind="error", content=error, metadata={"budget_exhausted": True}))
        output = partial_output or "(run budget reached)"
        if partial_output:
            # Do not persist unexecuted tool calls: providers require every tool
            # call in history to have a paired result on the next turn.
            messages.append(Message(role="assistant", content=partial_output))
        return AgentResult(
            success=False,
            output=output,
            iterations=iterations,
            trace=trace,
            error=error,
            usage=usage,
            messages=trim_to_complete_pairs(messages),
        )

    def _resolve_and_gate(
        self, tc, tools_by_name, before_tool, emit, *, budget=None, journal=None,
        run_id="", checkpoint=None, record_budget=None,
    ):
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
        if budget is not None:
            decision = budget.begin_tool()
            if record_budget is not None:
                record_budget("tool", decision)
            if not decision.allowed:
                raise BudgetExceeded(decision.reason)
        if checkpoint is not None:
            checkpoint()
        if journal is not None:
            journal.append_event(run_id, "tool_started", {"tool": tc.name})
        return tool, None

    def _record(
        self, tc, result, is_err, after_tool, emit, messages, *, budget=None,
        journal=None, run_id="", checkpoint=None,
    ):
        result = self._cap_result(result)
        if after_tool is not None:
            try:
                after_tool(tc.name, tc.arguments or {}, result, is_err)
            except Exception:  # noqa: BLE001 — a hook must never break the run
                pass
        emit(Step(kind="tool_result", content={"name": tc.name, "result": result, "is_error": is_err}))
        messages.append(Message(role="tool", tool_call_id=tc.id, name=tc.name,
                                content=result, is_error=is_err))
        if budget is not None:
            budget.finish_tool()
        if journal is not None:
            journal.append_event(
                run_id, "tool_finished", {"tool": tc.name, "is_error": bool(is_err), "result_chars": len(result)},
            )
        if checkpoint is not None:
            checkpoint()

    def _run_one(
        self, tc, tools_by_name, before_tool, after_tool, emit, messages, *, budget=None,
        journal=None, run_id="", checkpoint=None, checkpoint_after=None, record_budget=None,
    ):
        """Execute a single tool call (announce → gate → run → record)."""
        tool, blocked = self._resolve_and_gate(
            tc, tools_by_name, before_tool, emit, budget=budget, journal=journal, run_id=run_id,
            checkpoint=checkpoint, record_budget=record_budget,
        )
        if blocked is not None:
            messages.append(Message(role="tool", tool_call_id=tc.id, name=tc.name,
                                    content=blocked, is_error=True))
            return
        result, is_err = execute_tool_call(tool, tc.arguments)
        self._record(
            tc, result, is_err, after_tool, emit, messages, budget=budget, journal=journal,
            run_id=run_id, checkpoint=checkpoint_after if checkpoint_after is not None else checkpoint,
        )

    def _run_parallel(
        self, calls, tools_by_name, before_tool, after_tool, emit, messages, *, budget=None,
        journal=None, run_id="", checkpoint=None, record_budget=None,
    ):
        """Run a batch of read-only/idempotent tool calls concurrently. Tool calls
        are announced and gated up front (gates are non-blocking for these), the
        handlers run on a thread pool, and results are appended in original order
        so the assistant↔tool_call_id pairing the API needs is preserved."""
        from concurrent.futures import ThreadPoolExecutor

        plan = [
            (
                tc,
                *self._resolve_and_gate(
                    tc, tools_by_name, before_tool, emit, budget=budget, journal=journal,
                    run_id=run_id, checkpoint=checkpoint, record_budget=record_budget,
                ),
            )
            for tc in calls
        ]
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
                self._record(
                    tc, result, is_err, after_tool, emit, messages, budget=budget, journal=journal,
                    run_id=run_id, checkpoint=checkpoint,
                )

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

    # Marker that begins a compaction summary message. Recognised on later passes
    # so an existing summary folds through instead of nesting, and so callers can
    # spot it in the history.
    COMPACTION_PREFIX: ClassVar[str] = "[earlier context summarized:"

    def _maybe_compact(self, messages: list[Message], emit) -> None:
        """Keep the running history under the context budget.

        Two stages, both pairing-safe:

        1. **Truncate** the *content* of old ``tool`` messages in place (file
           dumps, command output) — reclaims the bulk of the tokens without
           removing any message, so assistant↔tool_call_id pairing is intact.
        2. **Summarize-and-evict** whole oldest turn-groups when truncation
           isn't enough. The evicted groups are replaced by ONE concise summary
           message ("[earlier context summarized: ...]") so the agent keeps the
           thread (task, files touched, tools used, decisions) but frees the
           space the raw turns occupied. The summary is deterministic and cheap
           by default; no model call, so tests stay offline. A group starts at a
           ``user`` message, so cutting at a user boundary never orphans a
           ``tool`` result, and the summary itself is a ``user`` message so the
           history still begins at a valid conversation start. The most recent
           group (the current turn) is always kept intact.

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

        # Stage 2: still over budget after truncating tool output, so fold the
        # oldest complete turn-groups (everything before the 2nd user message)
        # into a single summary message. We accumulate the evicted groups, then
        # write ONE summary in their place. The agent keeps continuity (task,
        # files touched, tools, last result) without the raw bulk. The summary is
        # itself counted toward the budget, so we keep evicting until the history
        # (summary included) fits or only the current turn remains.
        #
        # An existing summary at the front is folded into the next eviction (its
        # facts carry through via ``carried_task``), so repeated compactions stay
        # a single summary rather than nesting.
        evicted_groups: list[list[Message]] = []

        def _summary_tokens() -> int:
            if not evicted_groups:
                return 0
            return len(self._summarize_groups(evicted_groups).content) // 4

        while est_tokens() + _summary_tokens() > threshold:
            user_idx = [i for i, m in enumerate(messages) if m.role == "user"]
            # Stop when only the current turn-group remains (and any leading
            # summary we already placed). Folding more would drop the live turn.
            non_summary_users = [
                i for i in user_idx
                if not (messages[i].content or "").startswith(self.COMPACTION_PREFIX)
            ]
            if len(non_summary_users) <= 1:
                break
            # Cut everything before the 2nd real user message: that span is one or
            # more complete oldest groups (plus any prior summary at the front).
            cut = non_summary_users[1]
            evicted_groups.append([m.model_copy() for m in messages[:cut]])
            del messages[:cut]

        evicted = len(evicted_groups)
        if evicted:
            messages.insert(0, self._summarize_groups(evicted_groups))
        if compacted or evicted:
            note = []
            if compacted:
                note.append(f"truncated {compacted} old tool result(s)")
            if evicted:
                note.append(f"summarized {evicted} oldest turn(s)")
            emit(Step(kind="thought", content=f"[context compacted: {', '.join(note)}]"))

    def _summarize_groups(self, groups: list[list[Message]]) -> Message:
        """Deterministically fold evicted turn-groups into one summary Message.

        Cheap and offline by default: no model call. Captures the original task,
        the user asks, files touched, tools used, and the last assistant answer.
        These are the facts the agent needs to stay coherent after the raw turns
        are gone. A model-based summary could be slotted in here behind a flag, but
        the default path must never hit the network so tests stay offline.
        """
        user_asks: list[str] = []
        files: list[str] = []
        tools_used: list[str] = []
        last_answer = ""
        # Pull the previous summary's task line through so the original task is
        # never lost across repeated compactions.
        carried_task = ""
        for group in groups:
            for m in group:
                if m.role == "user":
                    text = (m.content or "").strip()
                    if text.startswith(self.COMPACTION_PREFIX):
                        carried_task = text  # an earlier summary, fold it through
                    elif text:
                        user_asks.append(text)
                elif m.role == "assistant":
                    if m.content and m.content.strip():
                        last_answer = m.content.strip()
                    for tc in (m.tool_calls or []):
                        tools_used.append(tc.name)
                        for key in ("path", "file_path", "filename", "file"):
                            val = (tc.arguments or {}).get(key)
                            if isinstance(val, str) and val:
                                files.append(val)
                                break

        def _digest(items: list[str], limit: int) -> list[str]:
            seen: list[str] = []
            for it in items:
                if it not in seen:
                    seen.append(it)
            return seen[:limit]

        parts: list[str] = []
        if carried_task:
            # Strip the wrapper so we don't nest "[earlier context summarized: ..."
            inner = carried_task[len(self.COMPACTION_PREFIX):].strip().rstrip("]").strip()
            if inner:
                parts.append(inner)
        if user_asks:
            asks = _digest(user_asks, 4)
            parts.append("asks: " + " | ".join(a[:200] for a in asks))
        if files:
            parts.append("files touched: " + ", ".join(_digest(files, 10)))
        if tools_used:
            parts.append("tools used: " + ", ".join(_digest(tools_used, 10)))
        if last_answer:
            parts.append("last result: " + last_answer[:300])
        body = "; ".join(parts) if parts else "older steps elided"
        return Message(role="user", content=f"{self.COMPACTION_PREFIX} {body}]")
