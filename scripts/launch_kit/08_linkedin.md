# LinkedIn

Audience: ex-colleagues, recruiters, founders, distant connections. Tone: more polished than Twitter, more substantive than the IH post. Length: ~250 words is the sweet spot.

---

I built a CLI for the most repetitive part of running a startup: the Monday-morning data-gathering ritual.

Every week I'd open Stripe (revenue, churn, failed payments), then Linear (urgent issues), then Slack threads, then assemble the same briefing into a doc by hand. The information already existed — I was just acting as a router.

So I made `csk briefing` — one command that pulls live from each service and produces a structured Markdown report: MRR trend, new/churned subs this week, failed charges to retry, top urgent engineering issues, computed action items. Auto-saves each run; next week's output shows the deltas inline.

A few design choices worth calling out:

• Read-only by default. Every integration is GETs only. Adding write paths requires going through an explicit `ApprovalGate` — no agent should refund a charge unsupervised.

• Works with any LLM. Built on Claude by default but switches to Ollama, OpenAI, Together, Groq, or Fireworks with one config change. Use a local OSS model and the whole thing runs without ever calling a cloud API.

• Prompt-injection scanning at the input boundary. The agent loop is hand-rolled (~150 lines) against the Anthropic SDK directly — not LangChain. Easier to debug, easier to reason about.

• Demo mode ships fake-but-realistic data so anyone can `csk init --demo && csk briefing` in 30 seconds without setting up any credentials.

Open source under MIT: https://github.com/rohithkandula19/Ronin

If you're a founder with Stripe and Linear, I'd love a 5-minute look at whether the report matches what you'd write yourself. The interesting feedback is "this is missing X."

#startups #opensource #claude #python #ai #cli #saas
