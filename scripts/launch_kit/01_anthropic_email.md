# Email — Anthropic recruiter / hiring manager

**To**: the Applied AI Engineer (Startups) recruiter or the hiring manager listed on the job posting
**Subject**: Applied AI Engineer (Startups) — built a working reference
**Tone**: confident, short, evidence-led

---

Hi <<Name>>,

I'm applying for the Applied AI Engineer (Startups) role. Rather than send the standard "here's my resume" note, here's the artifact I think speaks better:

https://github.com/rohithkandula19/Ronin

It's an open-source CLI (`csk`) that solves the Monday-morning founder data-gathering problem: pull live from Stripe + Linear + Slack + Notion + Postgres + GitHub, produce a structured briefing. The headline command is `csk briefing` — auto-saves each run, shows week-over-week deltas, posts to Slack. Built on Claude (or any OpenAI-compatible model — Ollama, Together, Groq) with a hand-rolled ReAct loop, not LangChain. Read-only by design, with prompt-injection scanning at the input boundary, approval-gate primitives for any future write paths, and a built-in LLM-as-judge eval suite with drift detection.

242 tests, all green on every push. ~6 weeks of evenings. MIT.

The reason I'm sending the artifact rather than just the link: I think this is roughly what Anthropic's startups team ends up writing for customers every quarter — a "what does production-grade Claude actually look like" template. I'd be excited to do that work full-time.

Happy to walk through any part of the codebase. 15 minutes whenever works.

— Rohith
<<LinkedIn URL>> · <<phone>>

---

## When to send

Day 1. Before any public post.

## After you send

If you get a reply with even one question, follow up within 4 hours. The half-life on recruiter interest is about a day.
