# Go-to-market — the business track (RI), as an internal planning draft

> **Status: planning draft, not claims.** This is the RI track from the master program —
> the *business* work, which is not code. Nothing here is executable and nothing here is a
> measured result. Every place a real number belongs is written as `‹placeholder›` on
> purpose: a go-to-market doc that invents traction is the same dishonesty the codebase
> refuses in a training metric. Fill the placeholders from real data before this leaves
> the repo. Positioning detail lives in [`../POSITIONING.md`](../POSITIONING.md); this doc
> is the plan for *taking that position to market*.

---

## 1. The one-sentence pitch

**Ronin is a masterless, provider-agnostic coding agent you can run for $0 on your own
keys — or embed in your editor, your CI, and your own product.** The wedge is honesty
about evidence: it does not claim a task is done until a verifier says so, and it does not
claim a benchmark it did not run.

Who it is for, in priority order:

1. **Individual engineers** who want a Claude-Code-style agent without lock-in to one
   model vendor — they bring their own key (any of Claude / Gemini / Cerebras / Groq /
   OpenRouter / Ollama) and pay nothing to the project.
2. **Teams** who want that agent behind their own trust boundary — self-hosted, gated,
   auditable — rather than shipping their codebase to a SaaS.
3. **Builders** who want the agent *as a dependency* — the SDK (`from ronin import Agent`),
   the MCP server, the ACP editor bridge, and the OpenAI/Anthropic-compatible `/v1`.

## 2. Why now / why us

- Provider fragmentation is real and growing; a masterless agent that treats the model as
  swappable is a durable position, not a feature.
- The trust story (approval gate, deny list, taint tracking, offline-testable everything)
  is the thing enterprises actually block on, and it is already built and gated by CI.
- The extension surface (skills, plugins with a consent gate, role workflows) makes it a
  platform, not a tool.

## 3. OSS launch checklist

The launch is the OSS launch; the product surfaces (cloud, teams) follow it, they do not
precede it. Gate each item on evidence, not vibes.

- [ ] **License + provenance clean.** MIT confirmed on the repo; every vendored asset and
      model base licence stated (the training base is Apache-2.0 for exactly this reason).
- [ ] **`pip install ronin` works from a clean machine** — the clean-install smoke test in
      CI is the proof, not a local check.
- [ ] **README headline numbers are generated, not hand-typed** (`generate_readme_stats.py`
      — already wired into CI).
- [ ] **Quickstart is copy-pasteable end to end** on a machine with no prior setup
      (`docs/site/quickstart.md`).
- [ ] **A 90-second demo** (asciinema or a short screen capture) of: install → point at a
      model → one real task with the approval gate firing.
- [ ] **CONTRIBUTING + a labelled "good first issue" set** (see
      [`../CONTRIBUTION_ROADMAP.md`](../CONTRIBUTION_ROADMAP.md)).
- [ ] **Security disclosure policy** (`SECURITY.md`) and a triaged view of the open
      Dependabot alert(s) before the repo is public.
- [ ] **Launch surfaces drafted**: HN "Show HN", a short blog post that leads with the
      honesty thesis, and a `r/LocalLLaMA` post (the local-model lane is a genuine hook).
- [ ] **Response plan**: someone owns the HN thread for the first 6 hours; a FAQ is
      pre-written for the predictable questions (why another agent, how is this different
      from Claude Code / aider / OpenHands, does telemetry phone home — answer: off by
      default, and `ronin telemetry show` proves it).

Metrics to watch (fill after launch, do not pre-invent): GitHub stars `‹n›`, install
count `‹n›`, first-week issues `‹n›`, % issues that are real bugs vs. usage questions.

## 4. Design-partner program

Goal: `‹target›` design partners (suggest 3–5) who run Ronin on real repos and give
structured feedback in exchange for direct support and influence on the roadmap.

- **Ideal partner:** a small eng team (5–30 engineers) already using an AI coding tool,
  blocked on either provider lock-in or on shipping code to a third-party SaaS.
- **The ask:** a 30-minute weekly sync for `‹n›` weeks, a shared issue label, and
  permission to quote anonymised outcomes.
- **What they get:** a direct line, priority on the fixes that unblock them, and the
  self-hosting story before it is generally available.
- **Outreach:** warm intros first; then a short, specific note (template in §6). Track in
  a simple pipeline (contacted → replied → scoping call → active → reference).
- **Success = a reference,** not a logo: one partner who will say, on the record, what
  changed for them.

## 5. Fundraising narrative (outline only)

Only if and when the OSS traction is real. The narrative is earned by §3/§4 evidence, not
by this outline.

1. **Problem:** AI coding is consolidating around single-vendor SaaS; teams that can't or
   won't ship their code out are stuck, and everyone is exposed to one model roadmap.
2. **Insight:** the agent should be masterless — model-swappable, self-hostable, and
   honest about what it verified.
3. **What's built:** (point at the repo and CI, not slides) a provider-agnostic agent with
   an enforced trust boundary, an extension platform, an SDK, and offline-reproducible
   evals. `‹traction numbers›`.
4. **Wedge → expansion:** individuals (OSS, $0) → teams (self-hosted, paid support/hosting)
   → platform (embed Ronin in other products via SDK/MCP/ACP/`/v1`).
5. **Why us:** `‹team›`.
6. **Ask:** `‹round size, use of funds›` — tie use-of-funds to specific roadmap items
   (RH cloud surface, the GitHub-App path, hosted inference), not headcount alone.

Do **not** put a revenue projection in a deck without the model behind it in a spreadsheet
you can defend line by line.

## 6. Templates

**Design-partner outreach (cold, keep it short):**

> Subject: Ronin — masterless coding agent, would value your eyes
>
> Hi `‹name›` — I'm building Ronin, an open-source coding agent that's provider-agnostic
> (bring your own key, or run a local model) and runs behind your own trust boundary
> instead of shipping your code to a SaaS. You mentioned `‹specific pain›`; that's exactly
> what it's for. Would you try it on a real repo and give me 30 minutes of honest feedback?
> Repo: `‹link›`. No ask beyond your time.

**Show HN (skeleton):**

> Show HN: Ronin — a masterless, provider-agnostic coding agent ($0 on your own keys)
>
> Lead with the honesty thesis (verifier-gated "done", generated benchmark numbers), the
> local-model lane, and the trust boundary. One GIF. Link the quickstart, not the pitch.

---

*This document is the RI track. The RE/RF/RG tracks (evals, training, hardening) are code
and live in the repo; RH (cloud/teams/GitHub-App product surface) is code that is largely
built with the GitHub-App webhook path as the remaining gap. This is the one track that is
deliberately prose, because the work it plans is not something software can do for you.*
