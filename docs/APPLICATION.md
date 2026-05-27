# Application note — Applied AI Engineer, Startups (Anthropic)

> A ready-to-adapt note built around **ronin**. Fill the `[bracketed]` bits with your
> own details. Keep it short — the repo does the talking.

---

## Short version (email / DM / cover note)

**Subject:** Applied AI Engineer (Startups) — I built a Claude-grade agent that runs on anything

Hi [name],

I'm [your name], [one line: e.g. "a developer who ships fast"]. I want to build with startups at Anthropic, so instead of a résumé I'll point you at what I made: **ronin** — an open-source, terminal-native Claude agent.

It's a coding/ops agent with the full Claude Code experience — streaming, gated edits with diffs, plan mode, MCP, subagents, project memory — **and** the things a startup actually needs that Claude Code doesn't: it generates images/video/voice, produces a Monday-morning founder briefing from Stripe/Linear/Slack, root-causes incidents across business data *and* code, and runs on **any** model (Claude when quality matters, free models when cost does).

Two things I think are most relevant to this role:
- **It has its own eval harness** — `ronin eval` scores the agent on objective tasks and works across providers, so "does the agent actually work?" is a number, not a vibe.
- **It's cost-aware and provider-agnostic** — the exact same agent runs on Claude or on a free tier, with automatic rate-limit handling. That's the reality of shipping AI at a startup.

Code (515+ tests, MIT): **https://github.com/rohithkandula19/Ronin**
60-second tour: [demo GIF / loom link]

Would love 15 minutes.

[your name] · [email] · [github]

---

## What ronin demonstrates (map to the role)

| The role wants… | ronin shows it |
|---|---|
| Building real agents on Claude | Hand-rolled ReAct loop, gated tools, streaming, plan mode, subagents — no framework crutches |
| Evals / knowing it works | `ronin eval` — objective, deterministic, cross-provider agent scoring |
| Working with Anthropic's ecosystem | First-class **MCP** client; provider abstraction with Claude as the default |
| Startup pragmatism | Founder briefings, `investigate`, cost-aware multi-provider (free → Claude), zero-config demo |
| Shipping & craft | 515+ tests, CI, packaged CLI, polished UX, MIT, building in public |
| Safety | prompt-injection scanner, read-only-by-default integrations, every write gated behind approval |

## Talking points if asked "why not just use Claude Code?"

- ronin isn't a Claude Code clone — it's Claude-Code-*grade* UX plus a startup's actual surface area (data, media, briefings, evals).
- It's a sandbox for the questions this role lives in: how do you measure agent quality? how do you make agents reliable on flaky free tiers? how do you wire MCP tools in cleanly? — all answered in code.

## Before you send
- [ ] Record the 60-second demo (`docs/demo/demo.tape` → GIF) and link it.
- [ ] Skim the README top-to-bottom as a stranger would.
- [ ] Make sure the repo is public and CI is green.
- [ ] Personalize every `[bracket]` — generic notes get ignored.
