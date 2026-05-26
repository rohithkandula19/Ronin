# Twitter / X variants

Pick **one**. Don't post all three.

---

## Variant A — output-led thread (recommended)

Attach a screenshot of `csk briefing` running, or the GIF from `scripts/demo.tape` (`brew install vhs && vhs scripts/demo.tape`).

**Tweet 1**:

> Built a CLI that gives founders a Monday-morning briefing from their Stripe + Linear data in 10 seconds.
>
> Revenue. Churn. Failed payments. Top urgent issues. Action items.
>
> One command. Read-only. Open source.
>
> `curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash && csk briefing`
>
> 👇 thread

**Tweet 2**:

> Here's the output from the demo dataset:
>
> [attach screenshot / GIF]
>
> Every section computed live from the tools you have configured. Missing a service? Degrades gracefully.

**Tweet 3**:

> It's also a general-purpose data-question CLI:
>
> `csk ask "which customers haven't logged in this month?"`
> `csk chat`  (multi-turn)
> `csk tui`   (full-screen)
>
> Each briefing run auto-saves. Next week's run shows "vs last week" deltas. `--history` shows the trend.

**Tweet 4**:

> Built on Claude (or any OpenAI-compatible LLM — Ollama, Together, Groq) with a hand-rolled ReAct loop. Read-only MCP servers for Stripe / Linear / Slack / Notion / Postgres / GitHub.
>
> 242 tests. MIT. No telemetry.
>
> github.com/rohithkandula19/Ronin

---

## Variant B — story-led single tweet

> Every Monday I'd open Stripe, then Linear, then Slack, then a doc, and write the same founder briefing by hand.
>
> Built a CLI that does it in one command. Live data. 10 seconds.
>
> `csk briefing`
>
> Open-source, read-only, MIT: github.com/rohithkandula19/Ronin

---

## Variant C — punchy hook

> If you're a founder, this should not be a five-app ritual:
>
> "what's our MRR / new subs / churn / failed charges / top urgent issues this week?"
>
> Made a CLI that answers it in one command.
>
> `csk briefing` → github.com/rohithkandula19/Ronin

---

## Tactics

- **Best time**: 9-11am ET, Tuesday-Thursday. Same windows as Show HN.
- **First-tweet rule**: never lead with "🚀" or "I just shipped". You're not announcing — you're showing.
- **Engagement bait that isn't gross**: in tweet 4 of Variant A, ask "what does your Monday founder ritual look like?" Tweets that ask a sincere question get more replies than tweets that don't.
- **Quote-tweet real users.** If someone replies "ran it on my data and it caught a churn I'd missed," quote-tweet them on the day-5 thread. Worth 100x your own copy.
- **Don't @-tag Anthropic in Variants A/B/C**. The tagged version is `09_anthropic_public.md`, posted separately when you've earned the audience.
