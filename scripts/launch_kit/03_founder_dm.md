# DM — founder friend (iMessage / Slack)

**Target**: a specific founder you know personally who runs a seed-stage B2B SaaS and uses Stripe + Linear. Not a blast.
**Tone**: ask for a favor, easy to skip, concrete.

---

hey — built a thing I think saves you 15 min every Monday. CLI that takes your stripe + linear keys and gives you a one-paragraph founder briefing: MRR, new/churned subs, failed payments, top urgent issues, action items. auto-shows "vs last week" deltas after the second run.

100% read-only, MIT, runs locally, you own the keys.

```
curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
csk init
csk briefing
```

need 5 min of your reaction — specifically:
1. which numbers feel right / wrong
2. what's missing that you check on mondays
3. would you keep using it?

owe you a beer. repo: github.com/rohithkandula19/Ronin

happy to screen-share if it's faster — 10 min and i'll do the setup with you.

---

## The screenshare offer is the conversion lever

Most founders will *say* they'll try it, then not. The screenshare offer takes you from ~5% conversion to ~30%.

## Pre-empt the API-key question

If they ask: tell them Stripe lets you make a *Restricted Key* scoped to `read` on customers / subscriptions / charges. Direct link: https://dashboard.stripe.com/apikeys/create. Linear personal API keys are at https://linear.app/settings/api. Both revocable in one click.

## The follow-up after they try it

> appreciate it. three quick questions while it's fresh:
>
> 1. on a 1–10 scale, how close was the briefing to what you'd write yourself?
> 2. one thing it should add — what's the first thing you wished you saw and didn't?
> 3. one thing it should cut — what's noise vs signal?
>
> even one-word answers are great.
