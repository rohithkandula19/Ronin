# Indie Hackers / r/SideProject / dev.to / r/SaaS

Long-form post. Optimized for "how I built it" energy + a clear ask.

**Title**: `Built a CLI that turns Stripe + Linear into a weekly founder briefing — 6 weeks of evenings, 242 tests, fully open source`

---

**The problem**: every Monday I'd open four apps and write the same data summary by hand. The info already existed. I was just acting as a router.

**The product**: a CLI called `csk` whose headline command is `csk briefing`. Pulls from your configured services (Stripe, Linear, Slack, Notion, Postgres, GitHub — all read-only) and produces a Markdown report: MRR, new/churned subs, failed payments, urgent engineering issues, action items. Auto-saves each run; subsequent runs show "vs last week" deltas inline.

```
curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
csk init --demo        # ships fake data — zero-config tour
csk briefing
```

**Stack**:
- Python 3.11+, hand-rolled ReAct agent loop against the Anthropic SDK (~150 lines, no LangChain)
- Works with any LLM — Claude default, but Ollama / OpenAI / Together / Groq / Fireworks one-line swap
- Typer + Rich for the CLI surface, Textual for a full-screen TUI mode (`csk tui`)
- Read-only MCP-server-shaped wrappers around 7 services; demo mode bundles realistic fake data
- Pydantic-typed agent traces — pipe them into Langfuse, your DB, or your demo UI
- 242 mocked tests; CI is `pnpm + uv` workspaces

**What I learned (the parts worth reading even if you don't care about the product)**:

1. The "killer command" framing was wrong for 5 weeks. I had a kitchen-sink "ask anything" CLI nobody could repeat. Cutting to one specific, weekly-habit command (`briefing`) is what made it shareable.

2. Demo mode is the most underrated feature. The fact that strangers can run it in 30 seconds without a Stripe key got more reactions than any technical detail.

3. Hand-rolling the agent loop instead of using LangChain saved more debugging time than I expected. ~100 lines of Python is more readable than a 10-deep abstraction stack — and a Claude-native tool built on a competitor's framework is a weird signal.

4. Read-only by default is a feature, not a constraint. Adding write paths through an explicit `ApprovalGate` is more work than dropping a one-line write method. That extra friction is exactly what makes the tool safe enough for founders to actually use.

5. The eval suite was a forcing function for finishing. "Drift detection" sounds fancy but is just `if criterion_score_delta < -0.5: exit 1`. Wire it into CI and you can never silently regress.

**Open source under MIT**, no telemetry, no signup. PyPI: `ronin-cli`. Repo: https://github.com/rohithkandula19/Ronin

**Looking for two things from this crowd**:
1. What's the briefing missing that you'd check first thing Monday?
2. Would you pay for a hosted version that runs the briefing on cron and posts to Slack? Trying to gauge if there's a business under this or if it's just a tool.

---

## Where to post

- Indie Hackers: `https://www.indiehackers.com/post/new` → Product community
- r/SideProject: explicitly built for this kind of post
- r/SaaS: lukewarm — try only if IH and r/SideProject did well
- dev.to: works as a longer-form variant; tag `python`, `cli`, `opensource`, `ai`
- r/Python: skip — they hate "AI" posts in 2026

## Don't cross-post the same day

24 hours apart minimum. If IH lights up, ride it. If it dies, post to r/SideProject the next day with no reference to IH.
