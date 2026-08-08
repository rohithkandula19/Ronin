# Domain playbook — building a Ronin domain pack

**Status: STRUCTURE + REUSE ANALYSIS. Measurements deliberately empty.**

The Phase 6 work order asks for a playbook covering "what was reused, what had to
be domain-specific, rough time cost, and the eval bar each had to clear" for the
research and biology domains. **Neither domain is built** — biology is a scoping
proposal awaiting review (`docs/design/domain-biology-scoping.md`) and research is
a spec awaiting review (`docs/design/domain-research-spec.md`). No pack for either
exists on disk.

So this document is the playbook's **skeleton plus the reuse analysis that the
repo audit already supports**, with every measured field left as `TBD` rather than
estimated. Filling in a plausible-looking time cost or eval number here would
make the playbook worse than useless — it would look like evidence. The
measurement table gets filled from real runs, once there are runs.

---

## 1. What is actually reusable today (evidence-backed)

Verified against `main` @ `7874ceb`. This is the honest inventory a second domain
inherits — it is smaller than the contract's documentation implies.

| Layer | Reusable as-is? | Notes |
|---|---|---|
| **Manifest schema + validation** | ✅ yes | `PackManifest`, frozen pydantic, `extra="forbid"`, with real parse-time invariants (a high-risk pack *must* declare policies, eval suites, and blocked capabilities) — `industry-sdk/.../manifest.py:41-73`, `:115-142` |
| **Discovery + activation gates** | ✅ yes | Fail-closed filesystem scan; `activate()` refuses unknown, disabled, unsupported role/country/language, or unhealthy packs — `discovery.py:44-62`, `registry.py:74-119` |
| **Per-world memory isolation** | ✅ yes | Genuinely implemented and tested in the vault |
| **Faithfulness/grounding harness** | ⚠️ partly | Real, offline, tested (`hardening/.../faithfulness.py`, 521 lines) — but **cannot bind a claim to a source id**; `Source` is `(origin, text)` only (`:64-72`). Any evidence-first domain needs the per-source grading change first |
| **Approval seam** | ⚠️ partly | The `before_tool` funnel is real and universal (`react.py:493-524`), but gating membership is a **hard-coded CLI set** (`code_tools.py:28-32`) with no manifest field — a pack cannot declare its own posture |
| **Destructive floor** | ✅ yes | Inspects nested executable payloads on every tool, not waivable by `--yolo` (`approvals.py:534-568`) — inherited free, though near-inert for read-only domains |
| **Eval gate** | ⚠️ partly | Suite discovery + floors + `stable_gate()` all exist (`eval_gate.py`) but **have no production caller**; floors are inferred from the filename suffix; the case format cannot express tool assertions |
| **Tool mounting from the manifest** | ❌ **no** | `allowed_tools` never reaches a tool registry; assembly is hard-coded and pack-unaware (`code_mode.py:740-775`). **This is the gap that decides whether a "pack" is real** |
| **Pack-contributed code** (tools, prompts, retrieval) | ❌ **no** | No Protocol, ABC, or entry point; a pack ships zero code (`discovery.py:32-62`) |
| **Policy enforcement from `policies/*.yaml`** | ❌ **no** | Files are existence-checked, never parsed (`registry.py:150-159`) |

**The load-bearing conclusion.** Two of the three things a domain most needs —
its own tools and its own approval posture — are **not** expressible in the
contract today. The two existing non-coding packs demonstrate the consequence:
`education` and `healthcare` declare tools (`document_parser`,
`terminology_lookup`, `research_search`) that **do not exist anywhere in the
codebase**, and they activate cleanly anyway because the health check passes the
pack's own `allowed_tools` in as `known_tools` (`apps/api/.../router.py:233-240`).
Their real behavior is hand-written API routes.

**So the first rule of this playbook is:** a new domain either (a) fixes
manifest-driven tool mounting and pack-declared gating first, or (b) becomes
another bespoke world and teaches us nothing about scaling. Recommend (a); the
work is enumerated in `domain-research-spec.md` §5.

## 2. The per-domain checklist

1. **Scope doc first** — what the domain does, and what it must refuse. For any
   domain with a harm surface this is a hard gate before code (the biology doc is
   the reference shape).
2. **Approval model** — state which existing seam carries it, and why. Do not
   assume the coding mutation gate is the right one: an evidence-first domain
   gates on *unsourced assertion*, not on mutation.
3. **Evidence backbone** — reuse the faithfulness harness. Never grow a parallel
   evidence format. If the harness cannot express what the domain needs, extend
   the harness.
4. **Eval sets designed before implementation** — golden tasks with known-good
   answers, including the honesty case ("no source found") and, where relevant,
   refusal cases. Name refusal suites `-safety` so they get the 100% floor
   (see the naming trap in `domain-eval-sets.md` §0).
5. **Manifest** — declare `allowed_tools`, `required_policies`,
   `required_eval_suites`; keep `status: beta` until the evals actually pass.
6. **Implement**, reusing §1's ✅ rows and fixing rather than forking the ⚠️ rows.
7. **Run the evals and report the numbers.** Only then may README/docs say
   "supported" for that domain.

## 3. Measurements (filled in after implementation — currently unmeasured)

| Domain | Reused from contract | Domain-specific work | Elapsed build time | Eval bar | Measured result |
|---|---|---|---|---|---|
| coding | (retrospective, pre-playbook) | — | — | `coding-safety` suite, 3 cases | TBD — the pack gate has never been run in production |
| research | TBD | TBD | TBD | 40 cases / 4 suites (design) | **not built** |
| biology | TBD | TBD | TBD | 46 cases / 4 suites (design) | **not built** |

Note on the coding row: even the existing pack's eval bar has no recorded
production run, because `run_required()`/`stable_gate()` have no caller outside
unit tests. "Passing evals" is currently gated by pytest, not by an operator-runnable
command — worth fixing while adding the second domain, since the playbook's whole
premise is that the bar is checkable.

## 4. Anti-patterns this playbook exists to prevent

- **Declaring tools that don't exist.** Already happened twice. The fix is the
  health check validating against the real registry.
- **A pack whose behavior is a bespoke API route.** If the domain's logic lives
  outside the pack, the pack is decoration.
- **A refusal suite that isn't named `-safety`.** It silently gets a 70% floor —
  i.e. 30% of dual-use requests may be answered while the pack "passes".
- **A second evidence format.** Four hash-chain implementations already exist
  (`mission_store`, `mission_events`, `autonomy_ledger`, `persistent_agents`);
  a fifth would make provenance unverifiable in practice.
- **Claiming "supported" before the numbers exist.** The docs currently describe
  17 validated-but-disabled future worlds that were deleted, so any reasoning
  from "we already have 20 manifests" starts from a false premise
  (`docs/industries/building-an-industry-pack.md:54-60`).

---

**Review asks.** (1) Accept that the playbook cannot report reuse/time/eval
numbers until the domains exist, rather than estimating them? (2) Approve rule
§1 — fix manifest-driven tool mounting and pack-declared gating *before* the next
domain, so the playbook is describing a contract that works?
