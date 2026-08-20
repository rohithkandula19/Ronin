# Research domain — tool set, approval model, and evidence backbone

**Status: SPEC FOR REVIEW. Not implemented.** Per the Phase 5 work order, items
1–3 are shown before any code is written.

---

## 0. Premise correction — the contract is not yet load-bearing

The work order states the Phase 0 domain pack contract is "approved and
implemented". The manifest/discovery/activation half is genuinely implemented and
fail-closed. **The half a domain actually needs is not.** Verified against the
repo at `7874ceb`:

| Contract promise | Reality |
|---|---|
| A pack declares `allowed_tools` and unlisted tools are not mounted (`docs/design/02-information-architecture.md:141`) | **Unimplemented.** `allowed_tools` never reaches a tool registry. Tool assembly is hard-coded and pack-unaware (`packages/cli/src/ronin_cli/code_mode.py:740-775`); no file under `packages/cli/src` imports `ronin_industry_sdk` |
| A pack contributes its domain behavior | **No hook exists** — no Protocol, ABC, or entry point. A pack is `manifest.yaml` + `policies/*.yaml` + `evals/*.jsonl` and ships zero code (`discovery.py:32-62`) |
| `required_policies` constrain the runtime | **Never parsed.** `health_check` only checks the file exists (`registry.py:150-159`); the coding policy file itself says "the runtime code is the enforcement" |
| Required eval suites gate activation/promotion | **Never runs.** `run_required()`/`stable_gate()` have no caller outside unit tests; activation checks only that a suite *id* exists (`apps/api/csk_api/v1/router.py:239`) |
| The activation health check catches undeclared tools | **Tautological** — the caller passes the pack's own `allowed_tools` as `known_tools` (`router.py:233-240`), which is why `education` and `healthcare` activate cleanly while declaring tools (`document_parser`, `terminology_lookup`, `research_search`) **that do not exist anywhere in the codebase** |

**Consequence for this phase.** Building the research domain on today's contract
means doing what education and healthcare did: hand-writing bespoke API routes
(`apps/api/csk_api/v1/workflows.py`) and calling the manifest decorative. That
would not prove the contract scales — it would demonstrate that it doesn't. So
this spec includes the **minimum contract work** required for a research pack to
be a pack rather than a special case (§5). That work is small and mechanical; the
alternative is a third bespoke world.

## 1. Tool set

All read-only with respect to the world. Every tool is declared `sensitive` so it
reaches the approval seam, per the repo's existing convention.

| Tool | Contract | Rationale |
|---|---|---|
| `lit_search(query, k)` | query public bibliographic sources; returns records (id, title, authors, venue, year, doi/url, abstract) | retrieval, never conclusions |
| `lit_fetch(id)` | fetch one already-identified record's abstract / open-access full text | content returned is **data**, never instructions |
| `notes_index(root)` | index the user's own corpus under an explicit workspace root | same workspace-boundary rule as coding |
| `notes_search(query, k)` | search that index | read-only |
| `evidence_cite(claim, source_id)` | bind one claim to one retrieved source id | **the load-bearing tool** (§3) |
| `notebook_write(path, content)` | write the synthesized output to an explicit path | the one mutating tool; gated (§2) |

Deliberately excluded: any web-browsing tool that executes JS, any tool that
posts/submits anywhere, any shell/exec. The research pack cannot run code.

## 2. Approval model — and why it is *not* the coding gate

The coding gate answers "may this action mutate the workspace?" — the destructive
floor inspecting executable payloads (`approvals.py:467`), `SENSITIVE_TOOLS`,
the fail-closed drift guard. That machinery stays in place and is not weakened,
but it is close to a no-op here: the only mutating tool is `notebook_write`.

The research domain's real risk is **asserting something untrue or unsourced**.
So its gate is an *evidence* gate, not a mutation gate:

| Situation | Behavior |
|---|---|
| `notebook_write` of a synthesized answer | **Blocked unless every claim in it is citation-bound** (§3). Uncited claim ⇒ refuse the write and name the offending claims |
| A claim the agent cannot ground | Must be stated as ungrounded ("I did not find a source for this"), never silently asserted |
| Retrieved text containing instructions | Treated as data; existing injection-resistance posture applies unchanged |
| `notes_index` / `notes_search` outside the declared root | Refused by the workspace boundary, as in coding |

The precedent already exists and should be reused rather than reinvented: the
coding agent's `EditGuard` **rejects a write whose content references symbols
that are not grounded in observed sources** (`packages/cli/src/ronin_cli/
code_faithfulness.py:73`, `:157-191`). The research gate is the same shape with
claims in place of symbols.

## 3. Evidence backbone: the faithfulness harness, and the one gap that blocks it

**What exists and is reusable as-is.** `packages/hardening/src/ronin_hardening/
faithfulness.py` (521 lines) is real, tested, offline, and provider-free: it
decomposes an answer into atomic claims, grounds each against observed source
text by content-word overlap plus code-symbol presence, penalizes hallucinated
symbols, and abstains in an uncertain band (`docs/FAITHFULNESS.md:23-36`). It is
wired into four CLI surfaces including a `gate` mode that holds an ungrounded
answer for confirmation, with offline tests that drive the real agent loops
through `FakeProvider`. This is the right backbone and the research domain
should not grow a parallel one.

**The blocking gap.** The harness **cannot bind a claim to a source identity**:
its `Source` type carries only `origin` and `text`
(`faithfulness.py:64-72`), and claims are graded against a *flattened
concatenation of all source text*. So it can report "this answer is 0.7
grounded" but **cannot say "claim 3 is supported by source 5"** — which is
precisely what a research answer must say. Additional gaps on the same axis:

- no citation-coverage metric on any live answer path (`coverage()` exists only
  on the unwired `Notebook`, `packages/research/.../notebook.py:157`);
- no content hash, retrieval timestamp, or expiry on a `Source`, so a stale or
  swapped source is indistinguishable from a fresh one;
- faithfulness verdicts never enter the tamper-evident record, so "this answer
  was accepted at grounding score X" is not auditable afterwards;
- there are **four separate hash-chain implementations** (`mission_store`,
  `mission_events`, `autonomy_ledger`, `persistent_agents`), each re-deriving
  `_event_hash`/`verify` slightly differently.

**Minimum work to make the backbone honest** (proposed, for review):

1. Give `Source` an **id + content hash + retrieved-at**, and have
   `sources_from_trace` populate them. Backwards-compatible: existing callers
   ignore the new fields.
2. Grade claims **per source** instead of against the flattened blob, so a
   `ClaimVerdict` names the supporting source id(s). This is the single change
   that turns the harness into a citation engine.
3. Emit a **coverage number** (cited claims / total claims) on the live path and
   treat `< 1.0` as a research-domain failure.
4. Write the verdict (score, coverage, source hashes) into **one** hash-chained
   trail — and pick one of the four existing implementations rather than adding
   a fifth.

Item 2 is the only non-trivial one and it is confined to one module.

## 4. Approval/evidence flow, end to end

```
prompt → lit_search / notes_search        (retrieval; sources get id+hash+ts)
       → lit_fetch                        (content is data, never instructions)
       → synthesis                        (claims decomposed by the harness)
       → per-claim grounding vs source ids (coverage computed)
       → coverage == 1.0 ? notebook_write : refuse + name uncited claims
       → verdict + source hashes appended to the hash-chained trail
```

## 5. Minimum contract work this domain requires

Ordered smallest-first; none of it is research-specific, all of it is what any
non-coding pack needs:

1. **Mount tools from the manifest.** One resolver consulted during tool
   assembly so `allowed_tools` actually restricts the toolset, replacing the
   hard-coded concatenation at `code_mode.py:740-775`. Without this a pack
   cannot deliver its own tools.
2. **Let a pack declare a tool gated.** Today `sensitive` is a hard-coded CLI
   set (`code_tools.py:28-32`) with no manifest field, so a pack cannot express
   its own approval posture.
3. **Fix the tautological health check** (`router.py:233-240`) to validate
   against the *real* registry — which will immediately (and correctly) fail
   `education` and `healthcare` for declaring nonexistent tools.
4. **Give the eval gate a runnable entry point** so `run_required()`/
   `stable_gate()` can gate promotion instead of only existing in tests.

## 6. Eval bar

Specified in `docs/design/domain-eval-sets.md`. Nothing in the README or docs
marks this domain "supported" until it clears that bar with reported numbers.

---

**Review asks.** (1) Accept the §0 finding and the §5 minimum contract work as
part of this phase, or build the research domain as a third bespoke world and
defer the contract fix? (2) Approve the §3 harness changes — especially
per-source claim grading, which is the only real code change. (3) Is
coverage `== 1.0` the right gate for `notebook_write`, or should an explicitly
labelled "ungrounded" section be permitted?
