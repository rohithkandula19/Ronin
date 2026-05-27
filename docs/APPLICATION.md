# Application note — Applied AI Engineer, Startups (Anthropic)

> Paste-and-send ready. Only the `[bracketed]` bits are yours to fill — name,
> contact, and the one personal line. Everything else is true of the repo today.

---

## The email (short — they skim)

**Subject:** Applied AI Engineer (Startups) — I built a Claude-grade agent that runs on anything

Hi [name],

I'm [your name] — [one honest line, e.g. "a developer who ships end-to-end and fast"]. Rather than send a résumé, I'll point you at what I built: **ronin**, an open-source terminal agent.

It's the full Claude Code experience — streaming, gated edits with diffs, plan mode, MCP, subagents, custom commands, project memory — **plus** what a startup actually needs and Claude Code doesn't: it generates images/video/voice, turns Stripe/Linear/Slack into a Monday-morning founder briefing, root-causes incidents across business data *and* code, and runs on **any** model — Claude when quality matters, free models when cost does.

Two things I think map directly to this role:

- **It knows whether it works.** `ronin eval` scores the agent on objective, deterministic tasks (no LLM judge) across providers — "does the agent work?" is a number, not a vibe. I also documented *why* naive cross-model benchmarking on a free tier is misleading, instead of hiding it.
- **It's cost-aware and provider-agnostic.** The same agent runs on Claude or a free tier, with automatic 429 back-off and smart model routing (cheap model for simple turns, strong for hard). That's the reality of shipping AI in a startup.

- **Repo** (526 tests, MIT): https://github.com/rohithkandula19/Ronin
- **60-sec demo**: the GIF at the top of the README
- **Benchmark + method**: [docs/BENCHMARK.md](https://github.com/rohithkandula19/Ronin/blob/main/docs/BENCHMARK.md)

Would love 15 minutes.

[your name] · [email] · [github] · [linkedin]

---

## Why this maps to the role (use in the email or the interview)

| The role wants… | ronin shows it |
|---|---|
| Building real agents on Claude | Hand-rolled ReAct loop, gated tools, streaming, plan mode, subagents, self-verification — no framework crutches |
| Knowing it works (evals) | `ronin eval` — objective, deterministic, cross-provider scoring + an honest benchmark write-up |
| Anthropic's ecosystem | First-class **MCP** client; provider abstraction with Claude as the default |
| Startup pragmatism | Founder briefings, `investigate`, cost-aware multi-provider + routing, zero-config demo |
| Shipping & craft | 526 tests, CI, packaged CLI, polished UX, demo GIF, MIT, built in public |
| Safety | prompt-injection scanner, read-only-by-default integrations, every write gated behind approval |

## "Why not just use Claude Code?" (you'll get asked)

ronin isn't a clone — it's Claude-Code-*grade* UX plus a startup's real surface area (data, media, briefings, evals), and it's a working sandbox for the questions this job lives in: how do you measure agent quality, make agents reliable on flaky free tiers, and wire MCP tools in cleanly. All answered in code, not slides.

## Final checks before you hit send
- [ ] Personalize every `[bracket]` — generic notes get ignored.
- [ ] Repo is public, CI is green, README GIF plays.
- [ ] Send it. The build is done; shipping it is the only step left.
