# v3 / v3b fine-tuning — evidence status and the protocol to finish it

**Status: EVIDENCE PHASE INCOMPLETE.** The v3b checkpoints exist but have never
been scored on the frozen runtime-parity eval set. Until they are, **v2 iter-150
(36/91) remains the recommended adapter** — that is the only frozen-set evidence
on file, and this repo does not promote adapters on hope.

## What is verified in git (this document trusts only this)

Checked against `origin/main` at `3b49dc1` and every remote branch:

| artifact | state |
|---|---|
| `ronin-training` package (parser → validator → split → evals → MLX config) | on `main` (PR #79) |
| EmbeddedProvider — Ronin runs on its own adapter, strict `<tool_call>` parser | on `main` (PR #82) |
| v2 sweep reports (`eval91_base.md` … `eval91_v2_iter300.md`) | on `main` — best: **v2 iter-150, 36/91** |
| v2 iter-150 category floor: `valid_tool_json` **0/4**, `grounding` **5/20**, `multi_turn_stability` **11/28**, `gate_respect` **6/12** | on `main` (`eval91_v2_iter150.md`) |
| branch `feat/v3-protocol-dialect`, commit `eae155e` (v3 corpus: executable tool-call dialect + read-first drills) | **NOT on GitHub — exists only on the training Mac** |
| frozen eval snapshot `training/eval_snapshots/eval91_frozen.jsonl` | **NOT in git** (local to that branch) |
| v3 checkpoint reports (`eval91f_v3_iter*.md`) | **NOT in git** (local to that branch) |
| v3b checkpoints `training/adapters/ronin_1.5b_v3b/*` | **never in git by design** (`training/.gitignore` excludes `adapters/`) |
| v3b eval reports / `sweep_v3b.log` | **do not exist anywhere yet — this is the unfinished work** |

Reported-but-unverified (stated by the operator, awaiting the pushed branch):
the v3 dataset grew to 834 SFT rows / 231 eval cases with 0 coverage gaps, and
no v3 checkpoint beat v2 iter-150 on the frozen set. Neither claim is treated
as evidence here until `feat/v3-protocol-dialect` is pushed with its reports.

## Why this cannot be finished from a cloud session

1. The adapters are gitignored binaries — they live only on the training Mac.
2. `mlx-lm` evaluation of these 4-bit checkpoints requires Apple Silicon.
3. The frozen eval set itself is on the unpushed branch.

Nothing here fabricates a score to route around that. The tooling below makes
the on-device run mechanical instead.

## The protocol to finish (on the Mac, `/Users/rohithkandula/ronin`)

```bash
git checkout feat/v3-protocol-dialect
# bring in the evidence tooling from this branch, then:
./training/scripts/finish_v3b_evidence.sh        # scores iter-300…1050 + final
                                                 # against the frozen set, writes
                                                 # eval91f_v3b_*.md + sweep_v3b.log,
                                                 # then aggregates automatically
```

The sweep is resumable (existing reports are skipped; `--fresh` redoes them),
refuses to run without the frozen set or the adapter dir, and stages raw
`0000NNN_adapters.safetensors` checkpoints into loadable `_ck/iNNNN` dirs the
same way the v2 sweep did.

The aggregator (`python -m ronin_training.aggregate_comparison`) then writes
`training/reports/v3_finetune_comparison.md`: full score table, the seven focus
categories per checkpoint, and a computed verdict — beats 36/91 or not,
`valid_tool_json` off 0/4 or not, grounding above 5/20 or not, multi-turn above
11/28 or not, gate regression or not. Missing reports render as MISSING; only
frozen-set (`eval91f_`) runs with matching case counts count as evidence.

## Decision rule (pre-committed, so the numbers decide — not the narrative)

- **Any v3b checkpoint > 36/91 on the frozen set** → adopt it, record the exact
  reproduction command (seed, iters, corpus commit) in the comparison doc,
  update `training/README.md` + `RONIN_ADAPTER` guidance, push branch, open PR.
  Adapter binaries stay untracked.
- **No v3b checkpoint > 36/91** → keep v2 iter-150, commit the failure evidence
  (reports + comparison doc) anyway — negative results are results — and change
  the training *design*, not the iteration count. In that case the next levers,
  in order of expected value:
  1. **Raw runtime-dialect format lock:** train on rows whose assistant turns are
     byte-identical to what `EmbeddedProvider`'s parser consumes (exact
     `<tool_call>` wrapper, exact JSON shape), generated *from* the parser's own
     serializer so train-time and run-time dialects cannot drift.
  2. **Parser-aware rejection filtering:** before training, run every candidate
     row's assistant output through the real runtime parser; any row the runtime
     would not execute is rejected from the corpus. If `valid_tool_json` is the
     blocking metric, the corpus must contain zero unparseable exemplars.
  3. **Smaller, purer tool-call-only dataset:** overweight short rows whose next
     token after the user turn IS the call (no narration preamble) until the
     format is reflexive; breadth can return later.
  4. **Preference/reward-style filtering for executability** (chosen = parses and
     names a registry tool with schema-valid args; rejected = narrated or
     malformed variant) — only once SFT alone has demonstrably plateaued.
  5. **Stronger Qwen base (7B) or full fine-tune** — only after `valid_tool_json`
     is off 0/4 at 1.5B. Scaling a model that cannot emit the dialect scales the
     failure, and the v2 evidence already showed data (not size) moves categories.

## Scope guard

Fine-tuning stays on Qwen (`mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit`)
until this evidence phase closes. `xai-org/grok-build` is a Rust coding-agent
runtime, **not a model**: it is reference material for Ronin's runtime design
only (see `docs/grok_build_runtime_reference.md`) and its audit must never mix
into this scorecard.
