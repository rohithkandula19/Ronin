# Show HN — first comment

Post this within 30 seconds of submitting. Goes in the "add comment" box on your own submission. HN convention: the title and URL alone are the pitch; the longer story lives in the author's first comment.

---

Author here. Built this because every Monday I'd open Stripe, then Linear, then Slack, then a doc, and reassemble the same summary by hand. So I made a CLI that does the assembly.

One command:

```
$ csk briefing
# Founder briefing — 2026-05-11

## 💰 Revenue
- MRR: $4,230 (ARR ~$50,760) · vs last week: +$340
- New this week: 2 · Churned: 1 — ⚠️ Pro customer, $588 ARR loss

## 💳 Payments (last 7 days)
- 28 succeeded · 2 failed · 1 refunded
- Failed charges to retry: cus_xxx ($49) — card_declined
- ⚠️ 1 subscription past due

## 🛠 Engineering
- Urgent open: 2 · High open: 5 · In-progress: 3
- ENG-101 Stripe webhook flake — Alice, In Progress

## ✅ Suggested action items
- Reach out to recently churned customers for exit interviews
- Retry failed payments / dunning for past-due subs
- Unblock or escalate every Urgent issue
```

Stack: hand-rolled ReAct agent loop against the Anthropic SDK (or any OpenAI-compatible endpoint — Ollama, Together, Groq, Fireworks), pluggable read-only MCP servers for Stripe / Linear / Slack / Notion / Postgres / GitHub. The briefing itself is pure-Python aggregation over those tools; the LLM is used for ad-hoc questions (`csk ask "..."`) and the optional natural-language framing of the briefing.

Notable bits if you're building agent stuff:

- Read-only by design. Every MCP server here exposes only GETs; there's an explicit `ApprovalGate` you'd have to wire to add writes.
- Prompt-injection scanner at the input boundary, PII redaction in traces, output validator with retry on Pydantic schema failures.
- Each briefing auto-saves to `.ronin/briefings/<date>.json`. Subsequent runs append a "vs last week" delta line at the bottom. `csk briefing --history` shows the trend table.
- `csk briefing --slack #founders` posts directly via `chat.postMessage`.
- Demo mode (`csk init --demo`) ships fake-but-realistic Stripe + Linear data so you can play with the briefing in 30 seconds without setting up keys. An offline keyword router answers `csk ask` queries without an LLM, so even the no-key demo is real.

Honest limitations:

- I've used it on my own data and on the demo dataset. It has not been used by anyone else yet. I am specifically here for the "your output is missing X" feedback.
- The "vs last week" deltas only make sense if you run it weekly. There's no scheduler — set a cron job.
- The Slack mrkdwn converter handles a small Markdown subset (bold, headings, bullets, code). Edge cases will look ugly.
- The agent will happily spend Anthropic tokens. `csk costs` tracks usage but I haven't built a daily cap.

Try the demo in 60 seconds:

```
curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
csk init --demo
csk briefing
```

Real config takes a Stripe Restricted Key (read-only) and a Linear personal API key. Both revocable in one click. Repo: https://github.com/rohithkandula19/Ronin

Happy to answer anything — and especially want to hear "the briefing should include X" or "X is noise."
