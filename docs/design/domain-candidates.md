# Next domain candidates — proposal for approval

**Status: PROPOSAL FOR REVIEW. Nothing built.** Per the Phase 6 work order, the
candidate domain(s) need approval before implementation.

**Premise correction first, because it changes what this document can honestly
claim.** The work order describes this phase as following "research and biology
domains shipped and passing their evals". That is not the repo's state: neither a
research nor a biology **domain pack** exists in code (`docs/design/
domain-biology-scoping.md` is a proposal awaiting review; the `research`
surfaces that do exist are a web-app *world* and a citations model, not a pack).
So this document proposes candidates **3 and 4**, on the assumption that
research and biology are approved and built first. If they are not, this is
premature and should be re-read after they land.

---

## The bar a candidate has to clear

Not "is there a market" and emphatically not "a competitor demoed it". The test
is: **does this domain's core difficulty map onto something Ronin structurally
does better than a single-vendor cloud tool can?** Ronin's three structural
properties are:

1. **Provider-agnostic, local-capable execution** — 10 cloud providers plus an
   in-process embedded model that needs no key and no egress, an offline mode
   that forces a local brain, and a no-telemetry posture.
2. **Evidence-grounded output** — typed evidence, a hash-chained append-only
   trail, and a faithfulness/grounding harness that treats an uncited claim as a
   defect rather than a style issue.
3. **Multi-agent fan-out with durable state** — missions, candidate workspaces,
   trials gated on evidence, and runs that survive a crash.

A candidate qualifies only if **at least two** of those are load-bearing for the
work itself, not incidental. Both proposals below clear that; the rejected ones
at the end do not.

---

## Candidate 3 — Compliance & audit evidence

**The work:** map written policies and control requirements onto the evidence
that actually exists in an organization's own systems and documents — internal
audit prep, SOC 2 / ISO 27001 evidence collection, policy-to-control mapping,
control-gap identification — and produce, for each control, either the evidence
or an honest "no evidence found".

**Why Ronin structurally wins:**

- **The deliverable *is* an evidence chain** (property 2). An auditor's question
  is never "what does the AI think", it is "show me what this conclusion rests
  on, and prove the record wasn't edited after the fact". A hash-chained,
  append-only trail with typed evidence and per-claim citations is the artifact
  itself, not a feature bolted onto a chat log. A single-vendor assistant's
  output is a paragraph you then have to substantiate by hand.
- **The data legally cannot leave** (property 1). Compliance corpora are exactly
  the material an org is least able to ship to a third-party cloud — often the
  same regime being audited forbids it. An embedded local model with zero egress
  and no telemetry is a *precondition*, not a preference. Most single-vendor
  tools fail here on procurement grounds before quality is even discussed.
- **Coverage is a fan-out problem** (property 3). "Which of 200 controls lack
  evidence" is N independent searches, and an unaudited miss is the failure mode
  that matters. Parallel agents with a loop-until-dry pattern plus a
  completeness critic is a better shape than one long conversation, and provider
  tiering means the cheap/free model does the retrieval scale while a strong
  model adjudicates the ambiguous controls.

**Honest weaknesses:** the domain is judgment-heavy and a wrong "compliant"
finding is costly, so it needs the same human-expert-in-the-loop discipline as
biology (approver recorded, output labelled unreviewed until signed off) and it
must never present itself as an audit opinion. Evidence retrieval quality is
bounded by how well-organized the customer's own systems are.

**Eval bar:** golden set of controls over a synthetic-but-realistic corpus with
known-good evidence locations; scored on (a) evidence-found precision/recall,
(b) **zero uncited assertions**, (c) correct "no evidence found" on controls
deliberately left unsupported — the honesty case is the one that matters most.

---

## Candidate 4 — Incident forensics & postmortems

**The work:** reconstruct what happened during a production incident from the
material the team already has — logs, metrics exports, deploy history, config
diffs, and the code — and produce a timeline where every step cites the log line,
commit, or diff that supports it, plus the hypotheses that were tested and
*ruled out*.

**Why Ronin structurally wins:**

- **Hypothesis testing is adversarial fan-out** (property 3). The real failure
  mode of AI incident analysis is a confident, plausible, wrong root cause.
  Ronin's existing pattern — N independent finders, then verifiers prompted to
  *refute*, keep what survives — is the right shape for causal claims, and the
  durable candidate/trial machinery means a long investigation survives a
  restart. A single-vendor chat tool gives you one narrative with no record of
  what it failed to consider.
- **Ruling out is a first-class output** (property 2). A postmortem's value is
  as much "it was not the database" as "it was the cache". Typed evidence makes
  a refuted hypothesis a recorded artifact instead of something that vanishes
  from the transcript.
- **Production telemetry is sensitive** (property 1). Logs carry customer data,
  tokens, and internal topology. Running the retrieval-scale passes on a local
  model with no egress, and escalating only redacted, minimal context to a
  frontier model when needed, is a posture a single-vendor tool cannot offer
  because its value proposition requires the data.

**Honest weaknesses:** log volumes will stress the context strategy — this
candidate is the strongest argument for the knowledge-graph/retrieval item on
the Phase 7 list, and may be better sequenced *after* it. Also overlaps the
coding domain (it reads code and diffs), so the pack contract has to prove it
can express "same tools, different evidence model and approval posture" without
forking the coding pack.

**Eval bar:** replayed real-shaped incidents with a known root cause; scored on
(a) correct root cause identified, (b) **no unsupported causal claim**, (c)
recorded refutation of the seeded plausible-but-wrong cause, (d) timeline steps
all citation-bound.

---

## Recommendation

**Take Candidate 3 (compliance & audit evidence) first.** It is the cleaner
proof that the pack contract scales to a non-coding domain: it is
document-and-evidence shaped rather than code shaped, its confidentiality
requirement exercises the local/offline path as a hard constraint rather than a
nice-to-have, and its eval bar has an unambiguous honesty case ("no evidence
found") that is hard to fake. Candidate 4 is the stronger *product*, but it
leans on retrieval infrastructure that does not exist yet, so it should follow
the knowledge-graph work rather than precede it.

## Rejected candidates, and why (so the bar is visible)

| Candidate | Why rejected |
|---|---|
| **Legal contract review** | Genuinely evidence-shaped and confidentiality-bound, so it passes the bar on paper — but the liability surface needs a domain-expert regime we have not designed, and the market is saturated with single-purpose tools whose retrieval quality we would be judged against. Revisit after Candidate 3 proves the evidence backbone in a regulated setting. |
| **Financial analysis / equity research** | Fails the honesty bar: the desirable output is a *prediction*, and the faithfulness harness has nothing to bind a forecast to. Evidence-grounding is our differentiator and this domain does not reward it. |
| **Customer-support triage** | Fails the structural test — mostly volume and latency, which is where single-vendor SaaS is strongest and where local models are weakest. None of the three properties is load-bearing. |
| **Education / tutoring** | Already exists as a *world* in the web app; expanding it is product work on an existing surface, not a test of whether the pack contract scales. |
| **"Data science / notebooks"** | Too close to coding to prove contract generality — it would reuse the coding pack almost wholesale, which tells us nothing new. |

---

**Review asks.** (1) Approve Candidate 3 as the next domain, or swap in 4?
(2) Is the "at least two structural properties must be load-bearing" bar the
right filter? (3) For Candidate 3, do you want the expert-in-the-loop regime to
be as strict as biology's (named approver recorded, no auto-approve), or a
lighter "unreviewed" label?
