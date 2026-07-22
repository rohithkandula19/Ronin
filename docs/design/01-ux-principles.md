# Ronin — UX Principles

> **Status of this document:** The design constitution. These principles are how the vision becomes pixels, motion, and copy. When two good ideas conflict, the one that better serves a principle wins. When a principle and a deadline conflict, escalate — do not quietly violate.

Ronin's interface has a name: **Sumi (墨, ink)**. The discipline is sumi-e — ink on warm washi paper. Warm neutrals, one restrained clay accent (`#a98467`), vast negative space, precise typography, near-silent motion. It is the deliberate antithesis of the neon-blue/purple-gradient AI aesthetic. Ronin is a masterless expert; the interface is a **dojo, not a cockpit**. Every principle below serves that discipline.

### How to read this document

Each principle has four parts: a **name** you can invoke in a design review ("that violates Worlds Are Walls"), a **maxim** to memorize, a **rationale** that explains the why, and a **this means / this does not mean** pair that turns the principle into a testable line. Principles 1–7 govern *trust and behavior*; principles 8–11 govern *the Sumi aesthetic*; principle 12 governs *control*. When they collide, the order below is the tiebreaker.

### Precedence

When two principles genuinely conflict, resolve in this order — higher wins:

1. **Safety** (Fail Closed) — a boundary that protects the user overrides everything, including beauty and speed.
2. **Honesty** (Honest Labels, Show Your Work) — we would rather be truthful than elegant.
3. **Control** (Approval Before Action, Autonomy Dial, Memory You Can Revoke) — the user's authorship of their own work.
4. **Calm** (Negative Space, Near-Silent Motion, One Accent) — the Sumi aesthetic yields only to the three above.

Beauty never wins over safety. Speed never wins over honesty. Cleverness never wins over the user's control. This ordering is not a suggestion; it is how ties are broken.

---

## 1. Approval Before Action

**Maxim:** Nothing consequential happens without a gate the user chose to open.

Ronin can act — send, run, commit, move, delete. The instant an action leaves the reversible and enters the real world, it must pass through an approval gate: a plain-language preview of exactly what will happen, what it will touch, and what it cannot undo. The gate is not a nag; it is the moment the user becomes the author of the action. Autonomy levels let the user widen or narrow which actions require a gate, per World — but the gate's *existence* is not negotiable for irreversible operations. Trust is earned by asking well, not by asking never.

- **This means:** a preview that shows the actual commands, recipients, or diffs — in the user's language — with a deliberate confirm.
- **This does not mean:** a generic "Are you sure?" modal that trains users to click through, or silent execution because the model was "confident."

---

## 2. Honest Labels Over Optimistic Ones

**Maxim:** Say what is true, not what sounds finished.

Every claim Ronin makes about its own work carries a status label: `VERIFIED` (checked and confirmed), `IMPLEMENTED` (done but not independently confirmed), `BLOCKED_*` (stopped, with a machine-readable reason like `BLOCKED_NEEDS_CREDENTIALS`). The label is not decoration — it is the epistemic contract. A calm, correct `BLOCKED_AWAITING_CI` is a better outcome than a cheerful "All done!" that turns out to be false. We optimize for calibrated trust, never for the momentary feeling of competence. Fabricated status is the cardinal sin.

- **This means:** distinct, legible labels wherever Ronin reports on its own actions, and a `BLOCKED_*` state that reads as a valid, respectable result.
- **This does not mean:** burying uncertainty in hedge-words, or showing a green checkmark for anything Ronin has not actually verified.

---

## 3. Worlds Are Walls

**Maxim:** Context is a boundary, and boundaries are visible.

A World is not a theme or a mode — it is an isolation boundary with its own tools, rules, and memory. The user must always know which World they are in and trust that its walls hold. Entering a World changes what is within reach and what is remembered; leaving it seals what stays behind. Crossing a wall — sharing memory or context between Worlds — is never automatic and never quiet. It is an explicit, visible act the user performs on purpose.

- **This means:** an unambiguous indicator of the current World, and a deliberate, consented handoff whenever context moves between them.
- **This does not mean:** a single blended context where a healthcare question can surface in a marketing draft, or "smart" cross-world suggestions the user never asked for.

---

## 4. Memory You Can See and Revoke

**Maxim:** If Ronin remembers it, you can point to it and delete it.

Memory is not a black box and not a hoard. Everything Ronin retains is visible in a ledger, scoped to its World, attributed to when and why it was learned. The user can read it, narrow it, and revoke any item — and revoke means *erased*, not *archived*. Memory is a governed resource with an off switch, not an ambient side effect of using the product. The default posture is minimal: remember what serves the work, nothing more.

- **This means:** a memory panel per World where every remembered item is inspectable and one action from deletion.
- **This does not mean:** invisible long-term storage, "personalization" the user can't audit, or a delete that merely hides.

---

## 5. Consent Is Opt-In, Recorded, and Revocable

**Maxim:** Off by default; and off means off.

Using the product is never consent to being trained on. Any use of a user's data for training requires opt-in that is explicitly given, recorded with a timestamp, and revocable at any time — after which the data is no longer used. There is no dark pattern, no pre-checked box, no "by continuing you agree." The absence of a recorded consent is a hard `no`, enforced by the system, not by good intentions.

- **This means:** a clear, unchecked consent affordance, a visible record of what was consented to and when, and a revoke that takes effect.
- **This does not mean:** consent bundled into onboarding, buried in terms, or inferred from usage.

---

## 6. Fail Closed, Gracefully

**Maxim:** When in doubt, stop — and make stopping feel safe.

Ronin's safety rules fail *closed*: if a constraint might be violated, the default is to refuse the action, not risk it. But a refusal is a design surface, not an error. A fail-closed stop should read as competence and care, offering the user the safe adjacent path — "I can't do X, but I can do Y" — rather than a dead end. Healthcare never diagnoses or prescribes; it informs. Legal output is stamped `DRAFT_REQUIRES_LEGAL_REVIEW`. Secrets are never logged. These are not warnings the user can wave away; they are walls, and we make walls feel like protection.

- **This means:** refusals written with respect and a concrete alternative, and hard domain limits (no diagnosis, no unreviewed legal advice) enforced without exception.
- **This does not mean:** an "override" button on a safety-critical boundary, or a refusal that dumps the user into a wall with no way forward.

---

## 7. Show Your Work, Show Your Sources

**Maxim:** Every claim can be traced to where it came from.

Output without provenance is a rumor. When Ronin states a fact, it shows the source; when it takes an action, it shows what it did; when it makes a decision, it shows the reasoning available. Provenance is not clutter to hide — it is the difference between an answer the user can stand behind and one they must independently re-verify. Sourcing is especially non-negotiable in Healthcare Information, Legal, Finance, Science, and Research, where an unsourced claim is worse than no claim.

- **This means:** citations, command logs, and diffs attached to the claims they support, one glance away.
- **This does not mean:** confident prose with no attribution, or provenance so buried it might as well not exist.

---

## 8. Negative Space Is a Feature

**Maxim:** Emptiness is designed, not leftover.

The most important element on a Ronin screen is often the space around what matters. Vast negative space is how we direct attention, signal calm, and refuse the cockpit. We do not fill the margins because we can. Each screen does one thing well, framed by room to breathe — like ink on washi, where the unpainted paper is part of the painting. Density is a choice we make only when the work genuinely demands it, and we make it reluctantly.

- **This means:** generous whitespace, one clear focus per view, and the courage to leave the rest of the screen quiet.
- **This does not mean:** dashboards of competing widgets, information stuffed to the edges, or "engagement" chrome.

---

## 9. Near-Silent Motion

**Maxim:** Motion informs; it never performs.

Animation in Ronin exists to explain change — where something came from, where it went, that a state shifted. It is quick, quiet, and restrained, the visual equivalent of a brush lifting cleanly from paper. Motion never celebrates, never bounces for delight, never draws attention to itself. If an animation's only job is to look impressive, it does not ship. The interface should feel like it is *breathing*, not performing.

- **This means:** brief, purposeful transitions that clarify cause and effect, respecting reduced-motion preferences.
- **This does not mean:** confetti, spring-bouncing modals, loading theater, or decorative motion that adds latency for spectacle.

---

## 10. One Restrained Accent

**Maxim:** Color is a scalpel, used once.

Ronin lives in warm neutrals — the palette of ink and paper. A single clay accent (`#a98467`) marks what is actionable, what is live, what demands the eye. Because we use it sparingly, it *works*: when clay appears, it means something. The moment we add a second and third accent, every color means nothing. We reject the rainbow-gradient AI aesthetic entirely — not as a style preference, but as a discipline. Restraint is legibility.

- **This means:** neutral surfaces and type, with clay reserved for primary action, live state, and the accent that must be seen.
- **This does not mean:** semantic-color soup, gradient meshes, or a palette that glows to feel "AI."

---

## 11. Typography Carries the Design

**Maxim:** If the words are set well, the interface is most of the way done.

In an ink-on-paper world, type is not a component — it is the architecture. Precise scale, honest hierarchy, generous line height, and restraint in weight do more work than any illustration or gradient could. Copy is part of the design: labels are exact, refusals are humane, status is unambiguous. We write and set text as carefully as we build features, because in Ronin the text often *is* the feature.

- **This means:** a deliberate type scale, impeccable hierarchy, and copy reviewed with the same rigor as code.
- **This does not mean:** decorative fonts, weight-and-color chaos to force hierarchy, or filler copy nobody edited.

---

## 12. Autonomy Is the User's Dial

**Maxim:** How much latitude Ronin has is always the user's choice, per World.

Ronin can operate anywhere from suggest-only to substantial independence — but the user sets that dial, per World, and can move it either direction at any time. Higher autonomy is something Ronin *earns* and the user *grants*, never something the product assumes. The current autonomy level is always legible, and raising it is a deliberate act with clear consequences. This is how trust scales without ever becoming presumption.

- **This means:** a visible, per-World autonomy setting the user controls, with the current level always in view.
- **This does not mean:** autonomy that creeps upward on its own, a global setting that ignores domain risk, or independence the user didn't grant.

---

## Anti-patterns we reject

These are the market's defaults. We refuse them on purpose.

- **The neon oracle.** Glowing gradients and cosmic purple that signal "AI magic." We are ink, not neon.
- **The confident liar.** Fabricated status, invented progress, green checkmarks for unverified work. Never.
- **The silent actor.** Actions taken without preview, approval, or a trace. Consequential means gated.
- **The memory hoard.** Invisible, unbounded, un-deletable retention dressed up as "personalization."
- **The consent dark pattern.** Pre-checked boxes, opt-out training, consent buried in onboarding or terms.
- **The one true chatbox.** A single blended context that pretends every domain is the same problem.
- **The cockpit.** Walls of dials, badges, and widgets competing for attention. We build dojos.
- **Motion theater.** Confetti, bouncing modals, and loading spectacle that trade the user's time for a feeling.
- **The override on the wall.** A button that lets the user bypass a safety-critical boundary "just this once."
- **The optimistic hedge.** Softening bad news into vagueness instead of naming it with an honest label.
- **The lock-in moat.** Tying the user to one model provider and calling the cage a feature.
- **Engagement chrome.** Streaks, nudges, and notifications engineered to pull the user back rather than serve the work.
