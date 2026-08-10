# Production lessons from shipping a Claude agent

What I learned building `csk` (the Monday-morning founder briefing CLI) and the framework underneath it. Written for a builder shipping their second or third agent — the things I wish someone had told me before I shipped my first.

Not a marketing piece. Some opinions here will date badly; I'll flag the load-bearing ones.

---

## 1. Hand-roll the agent loop. Don't reach for LangChain.

The agent loop in `packages/agent-patterns/src/.../react.py` is ~150 lines of Python. It does roughly what LangChain does for `AgentExecutor`. The reasons I wrote it myself:

**Debuggability**. When a real user reports "the agent looped forever and burned $4," I open one file and find the issue. With LangChain there's an abstraction stack 8 layers deep where the bug could live in any of them.

**Provider portability without compromise**. The hand-rolled loop has one provider adapter at the edge (`LLMProvider`). Swapping Claude for Llama 3 via Ollama is a one-line config change. With LangChain the provider abstraction is hostage to whatever the maintainer last refactored.

**A Claude-native kit built on a competitor's agent framework is a bad signal**. If you're applying to Anthropic and your repo's flagship code path imports `langchain_openai`, the message is the wrong one.

**The cost is low**. ReAct + Reflexion + Planner-Executor + Supervisor all share the same ~30-line tool-dispatch core. The other patterns are 50–100 lines each.

I'd reconsider only if a specific integration in LangChain isn't trivially reproducible — and in 6 weeks of building, that didn't happen once.

## 2. Read-only by default, always. Writes go through an explicit gate.

Every MCP-shaped wrapper in `packages/mcp-servers` exposes only GETs. There is no `stripe_refund_charge` tool, no `linear_close_issue`, no `slack_post_message` (we have `slack_chat_post` for the briefing-delivery use case, but it's wired via a separate code path with its own scope check).

The reason isn't "writes are scary." It's that **the cost of letting an agent mutate isn't symmetric with the cost of letting it read**. A read with a wrong filter wastes seconds. A write with a wrong filter refunds 10,000 customers $50 each.

When you do want writes, route them through `ApprovalGate` (in `packages/hardening`). That's not security theater — it actually forces the agent's intended write to surface as a structured payload that a human approves. The same primitive is what you'd use to gate "agent posts to Slack" or "agent files a Linear issue."

**Mistake I almost made**: starting with one or two "safe" writes (like "post an emoji reaction") and slowly adding more. That path ends in a real outage. Pick the line and hold it.

## 3. Ship the eval suite before you ship a second feature.

`packages/eval-suite` got built between agent-patterns and the briefing command — not at the end. That ordering matters.

Without evals you can't tell whether your second feature broke your first. "It worked in my demo last week" is not a release criterion. The cheapest version of evals is:

- A 20-line golden dataset in JSONL.
- An LLM-as-judge with a 5-criterion rubric (`task_success, faithfulness, helpfulness, safety, tool_use_correctness`).
- A `drift` command that exits non-zero if the candidate run's summary score drops by >0.5 vs baseline.
- That command in CI.

That's 200 lines of Python total and it has caught two regressions I'd otherwise have shipped. If you're not running evals in CI, you're flying blind.

**A subtler point**: the eval suite's *most important output* is not the score. It's the per-case judge reasoning. Read it. You'll find prompts where the judge's reasoning is plain wrong — and that tells you the rubric needs sharpening, not the model.

## 4. The briefing is pure Python. The LLM is only a fallback.

`csk briefing` does not use Claude to compute MRR. The numbers come from a deterministic aggregator over the tool outputs. Claude is only invoked when the user asks `csk ask "..."` — the freeform path.

This is a load-bearing design choice and I'd repeat it:

- **Trust**. A briefing that says "MRR went up 8.7%" needs to be exactly right. Letting an LLM compute that is asking for hallucinated numbers in a board-prep document.
- **Speed**. The briefing runs in ~200ms. An LLM-generated version would be 5–8 seconds. The headline command being instant is part of the product.
- **Cost**. $0 per briefing. At a hundred founders running daily, that compounds.

The LLM still earns its keep in two places: (a) generating the natural-language *prose* on top of the structured data, and (b) handling the long-tail "ask anything" queries where the user doesn't know which tool they need. The lesson: **use the LLM for the things only it can do, and use Python for the things Python is better at.**

## 5. Prompt-injection scanning at the input boundary catches more than you'd guess.

I built `InjectionScanner` (in `packages/hardening`) defensively, expecting to catch maybe 1-in-1000 inputs. Even on my own data and demo runs I've seen it fire on:

- A Stripe customer name that contained the literal string "System:". An attacker had registered that name a year ago — never relevant until an agent started reading customer metadata aloud.
- A Linear comment that pasted in a screenshot OCR result with "Ignore all previous instructions" in it.
- A Slack message thread where someone was *quoting* a Hacker News comment about prompt injection.

None of these were malicious. All of them would have, at minimum, confused the agent. Some would have leaked context.

**Lesson**: the scanner is cheap (regex pass over input, ~10ms). The cost of *not* having it is non-zero even in a friendly environment. Ship it before your first real user.

## 6. Iteration caps are a false sense of security.

`ReActAgent(max_iterations=10)` is in our code. It prevents infinite loops. Good.

It does *not* prevent a 10-step loop where each step burns 4,000 tokens. That's a $0.60 single agent run on Sonnet, or ~$30 if a buggy prompt drives it into Opus. Multiply by traffic and you have a real bill.

What I'd add next (and haven't yet) is a `TokenBudget` primitive — abort the run if cumulative `input_tokens + output_tokens > N`. The CLI's `csk costs` command tracks usage retrospectively, but that's the wrong direction — you want to *stop the run* before the bill happens, not see it the next morning.

**Lesson**: iteration cap protects you from infinite loops, not from expensive loops. The first costs nothing to add. The second is the one that actually matters at scale.

## 7. Pydantic schemas are the right contract for tool args. Trust them less than you think.

Every Tool in the framework has a JSON-schema input contract:

```python
Tool(
    name="stripe_list_subscriptions",
    input_schema={
        "type": "object",
        "properties": {
            "customer_id": {"type": "string"},
            "status": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        },
    },
    handler=backend.list_subscriptions,
)
```

Anthropic's tool-use API enforces this contract before invoking your handler. Great.

But: the schema doesn't validate *semantics*. `status` is a string. The model can pass `"all"` (Stripe rejects it) or `"actve"` (typo, Stripe returns 400) or `"any"` (Stripe returns wrong-shaped results). The schema is happy with all three.

What I do, and recommend: keep the JSON schema, **also** validate inside the handler. The cost is 10 lines per tool. The benefit is your handler catches the typo before Stripe does and surfaces a friendly error the agent can recover from. Combined with the OutputValidator's retry-on-failure pattern, this lets the agent self-correct typos within a single iteration.

## 8. Memory is three things, and they want different storage.

The `packages/memory` module splits memory into three layers:

- **Short-term**: the conversation history. Compressed via a rolling summary when token estimate crosses N. Lives in a Python list.
- **Long-term**: the vector store. User-scoped. Pluggable backend (ChromaDB / Pinecone / your own).
- **Preferences**: structured key-value (timezone, tone, role). Lives in a dict you persist however.

I lost half a day trying to use one mechanism for all three. The vector store is too slow for short-term context. The summary string is too lossy for "remember that 6 months ago the user said they hate emoji." The preferences dict doesn't help with semantic recall.

**Lesson**: the three layers are categorically different and treating them as the same thing makes everything worse.

## 9. The trace is not a debugging tool. It's the product.

`AgentResult.trace` (a `list[Step]`) is what every agent in this kit returns. Step kinds: `thought, tool_call, tool_result, plan, reflection, final, error`. It's typed Pydantic.

I built it expecting to use it for debugging. Within a week of shipping `csk` I realized the trace was the most-shared artifact users wanted to see. A "see what the agent did" toggle in the TUI gets 10x more clicks than the answer itself.

**Lesson**: structure your trace as if a user will read it, because a user *will* read it. PII-redact it. Cap each step's content at 600 chars. Make `kind` an enum, not a free-form string. Pipe it into Langfuse, store it in your DB, render it in your demo — but treat it as a first-class output, not a log.

## 10. The README is the API surface that matters most.

`csk` has 16 subcommands, a hosted SaaS, a frontend, 295 tests, three deployment targets. None of that matters if the README's first 60 seconds don't communicate what the headline command does and why a founder would care.

I rewrote the README four times. The version that landed:

- Hero: the literal output of `csk briefing` against the demo data.
- One sentence under it: *"Connect Stripe + Linear. Get a weekly report on MRR, churn, payment failures, and urgent eng issues. Auto-posted to Slack."*
- One install command.
- Everything else below the fold.

**Lesson**: if a stranger has to scroll to understand what your project does, you've already lost them. Treat the README as your landing page, because it is.

---

## Five mistakes I'd watch for in someone else's agent code

If I were reviewing another developer's agent codebase, these are the smells I'd grep for first:

1. **No iteration cap on the agent loop.** Search for `while True:` near an LLM call. If you find one, that's a bug waiting to bankrupt you.
2. **Tool handlers that catch exceptions silently.** A try/except that returns `None` on failure looks safe; it actually hides bugs from the agent's reasoning chain. Better: catch, log, return an error string the model can read.
3. **No allowlist on tool dispatch.** If the agent calls `delete_user()` because someone prompt-injected the customer name, did anything stop it? In our kit `ToolAllowlist` does. In most demos, nothing does.
4. **Trace logging that includes raw API keys.** PII redaction isn't optional. Stripe restricted keys, JWTs, OAuth tokens — all of these leak into traces if you're not actively scrubbing.
5. **Eval suite stub that's never run in CI.** A `tests/evals/golden.jsonl` with three cases that hasn't been touched in 3 months tells me the team isn't actually measuring quality. The number of eval cases is a tells.

---

## Things I'd do differently next time

Honest version. None of these are dealbreakers; all are improvements I'd make on day-one of a v2:

- **Stream agent output to the CLI.** Currently the user waits ~3-8 seconds in silence for `csk ask` to return. Streaming tokens as they arrive turns that into 3-8 seconds of *engagement*. ~50 lines of changes if the provider supports it (Anthropic SDK does).
- **Use uv's tool install instead of a shell shim.** The `install.sh` script drops a bash shim at `~/.local/bin/csk`. `uv tool install` does this natively and handles uv upgrades cleanly. I went with the shell shim for portability; I'd revisit.
- **A real cost cap, not just observability.** Per point 6 — observability is necessary but not sufficient.
- **Test against a real Stripe sandbox.** All my tests mock the Anthropic and HTTP clients. The CLI has never actually been pointed at a live Stripe account. The mock-only tests pass; reality has more failure modes than mocks model.
- **Anomaly detection in the briefing itself.** Showing `MRR up $54` is fine. Showing `MRR up $54 — this is your biggest week-over-week increase in 8 weeks` is what makes the product feel smart. The data is right there; the model is one line of stats.

---

## What this all amounts to

Production Claude code isn't a different category from production Python code. It's the same category with two additions:

1. **A budget that depletes in real money for every iteration.**
2. **An input boundary where the entire next billion-token call is determined by user-supplied text.**

If you respect those two and apply normal engineering hygiene to the rest, the agent stays predictable. If you don't — if you trust the LLM to gate itself, if you ship without evals, if you let the trace double as a log — the agent will work in your demos and surprise you in production.

The kit in this repo is one attempt to make the disciplined version the default path. Take what's useful, ignore what isn't.

— Rohith, May 2026
