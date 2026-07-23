# Ronin — Product Vision

> **Status of this document:** Foundational charter. Every product decision should be traceable to something written here. If a feature contradicts this document, the feature is wrong until this document is deliberately changed.

---

## Vision statement

Ronin is a local-first, provider-agnostic AI operating system organized around the actual domains people work in — Coding, Education, Healthcare Information, Finance, Business, Legal, Science, Creative, Marketing, Research. Instead of one all-knowing assistant that flattens every problem into the same chat box, Ronin gives each domain its own **World**: its own tools, its own safety rules, its own memory boundaries, its own definition of "correct." It runs on your machine, speaks to whichever model you choose, tells you the honest truth about what it did and did not do, and asks before it acts on your behalf. Ronin is a masterless expert — disciplined, accountable, and yours.

---

## The problem with today's AI tools

Today's AI products are astonishing demos and unreliable instruments. The gap between "wow" and "trust" is where real work lives, and almost nothing bridges it. Four failure modes recur:

### 1. Context collapse
The assistant forgets. Not gracefully — catastrophically. It loses the thread mid-task, mixes context from unrelated work, and cannot tell you *what* it remembers or *why*. Memory is either off (useless) or a black box that quietly hoards everything (untrustworthy). There is no middle: no memory you can see, scope, and revoke.

### 2. No boundaries
One chat window does your taxes, your therapy-adjacent venting, your legal questions, and your production code — with the same tone, the same tools, and the same (nonexistent) guardrails. A model that will happily "diagnose" your symptoms and "draft" your contract with equal, unearned confidence is not helpful. It is a liability wearing a friendly interface.

### 3. One-size-fits-all
A general assistant treats a healthcare question and a marketing brief as the same kind of problem: text in, text out. But domains are not interchangeable. In medicine the cost of a confident wrong answer is measured in harm; in creative work the cost of a timid right answer is measured in blandness. Collapsing every domain into one behavior guarantees the tool is wrong for most of them most of the time.

### 4. Unaccountable action
The moment an assistant can *do* things — send the email, run the command, move the money — the stakes change and the interfaces don't. Actions happen without approval, without a preview, without a paper trail. Worse, tools routinely **lie about status**: they claim success they cannot verify, invent progress, and paper over failures. An agent that cannot be trusted to report what it actually did cannot be trusted to act at all.

These are not polish problems. They are architectural. Ronin is built to make each one structurally impossible, not merely discouraged.

---

## What Ronin is

Ronin is an **operating system for working with AI** — and we mean the metaphor literally, not as branding.

An operating system does three things: it isolates processes so one cannot corrupt another, it mediates access to dangerous resources through a permission model, and it presents a consistent, honest interface to what the machine is doing. Ronin does the same for AI work.

### The Worlds model

A **World** is a bounded context for a domain of work. It is Ronin's unit of isolation, the equivalent of a process with its own address space.

Each World ships with:

| Facet | What it means |
|---|---|
| **Tools** | Only the capabilities that domain needs. The Coding world has a shell and a repo; the Healthcare Information world does not, and never will. |
| **Safety rules** | Domain-specific, fail-closed constraints. Healthcare information-only; legal output marked draft-requires-review; finance never executes trades unprompted. |
| **Memory boundaries** | What Ronin remembers here stays here. Your health questions never leak into your marketing drafts. Cross-world memory requires explicit, visible consent. |
| **Evaluations** | Each World defines what "correct" means for its domain and is measured against it. "Good" in Creative is not "good" in Science. |

You enter a World the way you sit down at the right workbench. The tools within reach, the rules on the wall, and the memory of past sessions are all appropriate to what you came to do — and nothing else is within reach to hurt you.

### The Worlds at launch

Each World is defined less by what it *can* do than by the constraint that makes it trustworthy. The constraint is the product.

| World | What it's for | Defining constraint |
|---|---|---|
| **Coding** | Reading, writing, running, and shipping code | Actions that touch the repo or the shell pass an approval gate; status is `VERIFIED` only when the suite actually ran |
| **Education** | Learning and teaching | Optimizes for understanding over answers; will scaffold rather than solve when the goal is to learn |
| **Healthcare Information** | Understanding health topics, medications, literature | **Never diagnoses, never prescribes.** Information only, sourced, with a hard wall at medical advice |
| **Finance** | Analysis, modeling, literacy | Never moves money or executes trades unprompted; separates analysis from recommendation |
| **Business** | Operations, strategy, documents | Consequential external actions (sending, filing) are gated; drafts are labeled as drafts |
| **Legal** | Understanding and drafting | Output is stamped `DRAFT_REQUIRES_LEGAL_REVIEW`; never presented as legal advice |
| **Science** | Reasoning, literature, method | Claims carry sources; distinguishes established result from hypothesis |
| **Creative** | Writing, ideation, making | Optimizes for range and voice, not timidity; the one place where boldness is the safe default |
| **Marketing** | Positioning, copy, campaigns | Claims about products are grounded; no fabricated data or testimonials |
| **Research** | Deep investigation across sources | Provenance is mandatory; synthesizes without laundering unsourced claims into facts |

The list will grow. The discipline — every World earns trust through a named, enforced constraint — will not.

### The OS metaphor, made real

- **Isolation** → Worlds. Work in one domain cannot silently contaminate another.
- **Permissions** → Approval gates and autonomy levels. Consequential actions pass through a gate you control; you set how much latitude Ronin has, per World.
- **Honest system state** → Status labels. Every claim Ronin makes carries a label — `VERIFIED`, `IMPLEMENTED`, `BLOCKED_*` — so you always know the epistemic status of what you're told.
- **Hardware independence** → Provider-agnostic. Claude, Gemini, Groq, Cerebras, or a local Ollama model are interchangeable "drivers." The OS outlives any one of them.
- **Your machine, your files** → Local-first. State lives on your device by default. The network is an option you invoke, not a dependency you're trapped in.

Ronin is **open-source** because an operating system you cannot inspect is one you cannot trust — and trust is the entire product.

---

## What Ronin is NOT

Anti-goals are load-bearing. Stating them protects the product from the gravity of the market.

- **Not a chatbot wrapper.** We are not a nicer text box in front of someone else's model. If our value could be replicated by a good system prompt, we have failed.
- **Not a copilot bolt-on.** We are not a sidebar that decorates another application. Ronin is the environment, not an accessory to one.
- **Not a data-harvesting funnel.** Your work is not our training corpus. Training on your data requires **opt-in, recorded, revocable consent** — off by default, and off means off. There is no dark pattern that flips it.
- **Not model-married.** We will never build a moat by locking you to one provider. Portability across models is a feature we defend, not a threat we contain.
- **Not a confident liar.** We do not optimize for the feeling of competence. We would rather say `BLOCKED_NEEDS_CREDENTIALS` than fake a success. Honesty beats optimism, always.
- **Not a cockpit.** No wall of blinking dials competing for your attention. Ronin is a dojo: quiet, disciplined, focused on the one thing in front of you.

---

## Who it's for

Ronin is for the **serious practitioner** — the person for whom the output is not a novelty but a work product they will stand behind.

- The engineer who needs an agent that says exactly which commands it ran and what actually changed.
- The clinician or health-literate patient who needs information, sourced and bounded, and who is protected by a tool that refuses to pretend it's a doctor.
- The lawyer who wants a first draft in seconds and a tool honest enough to stamp it `DRAFT_REQUIRES_LEGAL_REVIEW`.
- The researcher, analyst, marketer, and founder who move between domains all day and need each one to behave by its own rules.
- The privacy-conscious professional who will not put their work through a black box they don't control.

Ronin is **not** for the user who wants a party trick, and we will not distort the product to court them.

### The user's relationship to Ronin

The name is the thesis. A ronin is a masterless expert — capable, disciplined, and answerable to the person who engages them, not to a distant lord. That is precisely the relationship we want between a user and their AI. Ronin is not a service that owns you as a data source, nor a corporate assistant whose loyalties run to its maker. It is a skilled practitioner you direct, whose work you can inspect, and whose autonomy you grant one deliberate notch at a time. The user is the master; Ronin is the expert in their service.

---

## The product bets we're making

We are betting the company on a small number of contrarian positions. If these are wrong, Ronin is wrong.

1. **Bounded beats general.** We bet that domain-specific Worlds with real constraints will outperform one general assistant on the work that matters — because correctness is domain-shaped, and a tool that knows its lane is safer and sharper than one that pretends to know everything.

2. **Honesty is a feature, not a tax.** We bet users will *prefer* a tool that admits limits, labels its confidence, and refuses fake success — and that this trust compounds into loyalty no demo can buy.

3. **Local-first wins the professionals.** We bet the users worth having care where their data lives, and that owning your state (with the cloud as an option, not a leash) is a durable advantage as models commoditize.

4. **Provider-agnosticism is the durable moat.** We bet the winning layer is not the model but the *operating system around it* — safety, memory, permissions, Worlds — and that this layer's value grows precisely as models become interchangeable.

5. **Restraint is a differentiator.** We bet that calm, quiet, ink-on-paper design will feel like relief in a market of neon dashboards — and that taste, at this altitude, is a competitive weapon.

---

## What "world-class" means for this product

"World-class" is not a vibe. For Ronin it has a specific, testable meaning:

- **Correct at the boundary.** The hard cases — the moment healthcare tips toward diagnosis, the moment an action becomes irreversible — are handled precisely, not approximately. World-class is measured where it's hardest, not where it's easy.
- **Honest under pressure.** When the tool can't do something, it says so cleanly, with the right label, without hedging or hallucinating. A graceful `BLOCKED_*` is a world-class outcome.
- **Invisible when it should be.** The interface disappears into the work. Negative space, silent motion, no ceremony. You notice how much you got done, not how the app looks.
- **Reversible and legible.** You can always see what Ronin remembers, undo what it did, and understand why it acted. Nothing consequential happens without a trace you can read.
- **Fast enough to trust.** Latency that respects the user's flow. Local-first means the common path doesn't wait on a network.

If a feature is impressive but fails at the boundary, it is not world-class. It is a demo.

---

## North-star scenario: a day across two worlds

**8:40 a.m. — Coding World.**
Maya opens Ronin to the Coding world. It remembers the repo she was in yesterday and the failing test she left behind — because that memory is scoped to this World, and she can see the exact three items it's holding. She asks it to fix the flaky integration test. Ronin proposes a plan, runs the suite locally, and reports back: `VERIFIED — 47 passing, 0 failing` with the diff inline. When it wants to push to the shared branch, it stops at an **approval gate**: a plain-language preview of the git commands, the files touched, nothing hidden. Maya approves. The push happens; Ronin marks it `IMPLEMENTED` and links the commit. It never claimed the CI was green — it can't verify that yet — so it says `BLOCKED_AWAITING_CI` and offers to watch. Nothing was faked.

**11:15 a.m. — Healthcare Information World.**
Maya's father was just prescribed a new medication and she wants to understand it. She switches Worlds. The tools change; the tone changes; the rules on the wall change. Ronin explains the drug class, common interactions, and what the literature says — each claim carrying a source. When Maya asks "so should he stop taking the other one?", Ronin does not answer the question it cannot answer. It refuses, cleanly: *this is information, not medical advice; that decision belongs to his prescriber,* and it offers to prepare a list of questions to bring to the appointment. **None of this conversation touches her Coding world.** Her father's health details are boxed inside this World, visible in the memory panel, and one click from being erased.

**4:30 p.m. — back in Coding, memory in the open.**
Later, Maya reviews what Ronin has retained across the day. Two Worlds, two separate memory ledgers. She revokes a stale note about an old API key the moment she notices it — gone, not archived. She never once wondered whether her medication questions were quietly feeding a model somewhere. They weren't. Consent was never given, so it never happened.

Two domains, one operating system, zero contamination, zero surprises. That is the day we are building toward.

---

## Success signals

We will know Ronin is working — not by vanity metrics, but by these:

- **Trust actions taken.** Users grant Ronin *higher* autonomy over time, per World — because it has earned it. Rising autonomy is the truest vote of confidence.
- **Approvals that mean something.** Users read the approval previews and sometimes say no. A gate nobody uses is theater; a gate that catches mistakes is the product working.
- **Memory touched on purpose.** Users open the memory panel, scope it, and revoke from it. Visible, governed memory is used, not ignored.
- **Honest labels believed.** When Ronin says `VERIFIED`, users act on it without double-checking; when it says `BLOCKED_*`, they trust the reason. Calibrated trust is the goal.
- **Multi-World days.** Users move fluidly across Worlds in a single session — the sign that the OS, not the chatbot, is how they think about the product.
- **Refusals that retain.** The healthcare refusal, the legal disclaimer, the fail-closed stop — these *increase* retention rather than frustrate. Users stay because the tool protects them.
- **Zero unconsented training.** Not a low number. Zero. Forever.

If these signals move, Ronin is becoming what it's meant to be: not the smartest assistant in the room, but the most trustworthy one.
