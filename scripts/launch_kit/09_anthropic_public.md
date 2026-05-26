# Public post tagging Anthropic — Day 4 only

**Only post this if Show HN got real traction on Day 3.** Without that social proof, the tag reads as desperate. With it, it reads as confident.

**Channel**: Twitter (X). Don't cross-post to LinkedIn — the tag-bait feel doesn't work there.

---

Shipped an open-source reference for production-grade Claude agents — `csk`: a CLI that does a weekly founder briefing from Stripe + Linear, plus ad-hoc data questions. Hand-rolled ReAct loop, prompt-injection scanning, read-only MCP servers, LLM-as-judge eval suite with drift detection, multi-provider, 242 tests.

Wrote it as a portfolio piece for my @AnthropicAI Applied AI Engineer (Startups) application. The argument: this is roughly what the startups team writes for every new customer engagement — a "what does Claude in production look like" template, batteries-included.

Built in 6 weeks of evenings. MIT. Demo runs zero-config.

`curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash && csk briefing`

github.com/rohithkandula19/Ronin

cc <<insert two Anthropic technical-staff handles you've engaged with before, e.g. @AlexAlbert__, @erikschluntz>>

---

## Things to know about this post

- **It's a confident move.** Risk: lands as trying too hard. Upside: the team sees it before the application surfaces through normal channels, and one boost makes the application inbound.
- **Tag at most two people**, both ones whose work you've genuinely engaged with. Tagging five is spam; tagging zero loses the play.
- **The "applying for the role" frame matters.** Without it, this is "look at my project." With it, it's a candidate showing their work. Different.
- **Don't follow up if no one boosts.** The post stands on its own.

## Optional follow-up tweet (only if it gains traction)

> the design constraint I'm proudest of: every integration here is read-only.
>
> the kit ships an `ApprovalGate` primitive — if you want to add write paths, you have to go through it. an agent refunding a charge unsupervised isn't a feature i want to make easy.
>
> [link to the hardening package]
