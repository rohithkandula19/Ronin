# Phase 7 vulnerability triage — evidence and outcome

Date: 2026-07-24 · Branch: `chore/harden-and-stage` (off PR #118 head `dd0c8f3`)

**Source of truth.** The Dependabot alerts API is not reachable from this
execution environment (403 — API access is MCP-brokered and no Dependabot tool
exists), so the enumeration below comes from the same advisory databases
Dependabot reads: `pip-audit` (OSV/PyPA) against the Python manifests and
`pnpm audit` (GitHub Advisory DB) against `pnpm-lock.yaml`. Counts therefore
may differ slightly from the Dependabot UI (Dependabot dedupes per-advisory,
OSV lists PYSEC+CVE aliases separately) — but every fix targets a real,
listed advisory, not a guess.

## Before (open advisories by manifest)

| manifest | tool | open | severity split |
|---|---|---|---|
| `uv.lock` (runtime workspace, 220 locked deps) | pip-audit | **0** | — |
| `training/cuda/requirements.txt` | pip-audit | **41** | `torch==2.6.0`: 23 · `transformers==4.51.3`: 18 (incl. Trainer RCE PYSEC-2026-2289, deserialization RCEs, ReDoS) |
| `pnpm-lock.yaml` (apps/web) | pnpm audit | **3** | 2 high (`sharp@0.34.5` < 0.35.0, `postcss@8.4.31` ≤ 8.5.11 — both transitive via `next`) · 1 moderate (`postcss` < 8.5.10) |

Blast-radius note: the 41 Python advisories live **only** in the pinned GPU
training stack — never installed by the agent runtime, CI, or the test suite
(`uv.lock` is clean). The 3 JS advisories are in the deployed web app's tree.

## Fixes

| alert group | fix | delta |
|---|---|---|
| `sharp` HIGH + `postcss` HIGH/moderate | pnpm `overrides` in root `package.json`: `postcss@<8.5.12 → >=8.5.12`, `sharp@<0.35.0 → >=0.35.0`; lockfile regenerated | pnpm audit **3 → 0** |
| `torch` (23 advisories) | `2.6.0 → 2.13.0` (max first-patched across fixable advisories) | pip-audit torch: **23 → 0** |
| `transformers` (18 advisories) | `4.51.3 → 5.14.1` (Trainer RCE floor is 5.3.0 — only a major clears it) | pip-audit transformers: **18 → 0** |
| ride-alongs (no advisories of their own; transformers 5.x needs current majors) | `trl 0.17.0→1.9.0`, `peft 0.15.2→0.19.1`, `datasets 3.5.0→5.0.0`, `accelerate 1.6.0→1.14.0`, `bitsandbytes 0.45.5→0.49.2` — full set proven mutually resolvable with `uv pip compile` (81 packages, clean) | — |

Code kept in step with the majors: `from_pretrained(torch_dtype=…)` →
`from_pretrained(dtype=…)` (transformers 5 rename) in
`training/cuda/train_cuda.py` and `training/src/ronin_training/eval_runner.py`.
The trl surface the trainer uses (`SFTConfig(max_length=…, eval_strategy=…,
dataset_text_field=…)`) is already the current API.

## After

| manifest | open advisories |
|---|---|
| `uv.lock` | 0 |
| `training/cuda/requirements.txt` (new pins) | **0** (pip-audit, `--no-deps`, all 7 core pins) |
| `pnpm-lock.yaml` | **0** |

**Remaining open: 0.** Nothing deferred.

Honest limits: the bumped stack is verified by (a) full dependency resolution,
(b) pip-audit clean, (c) API-rename review of every GPU-path call site, and
(d) the offline test suite. The actual GPU training/eval run on the new pins
happens on the free Colab T4 (this box has no GPU/torch) — any residual
runtime incompatibility would surface in Cell 3 and is fixable in the
notebook path without touching the corpus or the gate.
