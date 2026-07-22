# Fine-Tuning Build — Phase 0 Discovery

Prepared per the Ronin Volume-Driven Fine-Tuning Build Spec, Page 20 ("Phase 0 —
Discover … no implementation changes yet except creating `training/reports`").

**Status: discovery only. No code was changed. This report names exact files and,
crucially, corrects the spec's greenfield assumption — Ronin already ships most of
this pipeline.**

## 1. Hardware (verified, not assumed)

The spec asserts "Apple M2, 8 GB, macOS 26.5.1." Confirmed by `sysctl`:

| | Detected |
|---|---|
| chip | Apple M2 |
| memory | 8 GB unified |
| os | macOS 26.5.1 |
| arch | arm64 |

MLX is **not installed** in the dev env (`import mlx` → ModuleNotFoundError); it is
an optional on-device dependency. `mlx-lm[train]` would need installing to train.

## 2. What Ronin ALREADY has (the spec assumes greenfield; it is not)

| Spec asks to build | Already exists | Where |
|---|---|---|
| Dataset pipeline (→ JSONL) | **`ronin finetune`** — sessions → PII-scrubbed JSONL + a runnable QLoRA `train.py` + Colab/Modal/RunPod instructions. Pure + unit-tested. | `packages/cli/src/ronin_cli/finetune.py` (752 loc): `trace_to_example`, `build_dataset`, `to_jsonl`, `training_script`, `collect`/`script`/`serve` cmds |
| Secret/PII scrubbing of training data | Reuses the index redactor | `finetune.py` → `session_search.redact_for_index` |
| Eval harness / "score the base model" | **`ronin eval`** — objective, deterministic, provider-agnostic agent-quality tasks (file created? tool used? right answer?) | `agent_eval.py`: `EvalTask`, `run_eval`, `default_tasks` |
| Broader eval framework | suite / judge / drift / swebench / report | `packages/eval-suite/src/ronin_eval_suite/*` |
| Tool registry + schemas (Volume II) | **`build_code_tools`** already defines 11 tools (read/list/search/write/edit/multi_edit/run_command/run_background/…) with JSON schemas | `code_tools.py`, `bg_processes.py` |
| Approval-gate BEHAVIOR (Volume III) | **Enforced in the runtime**, not just trainable — the destructive floor + universal approval gate | `approvals.py` (`is_floored_tool_call`, `is_destructive_command`), `code_mode.py` gates |
| Local model serving | Local provider + MLX/Ollama serving | `embedded_provider.py`, `local_setup.py`, `runner.py` (mlx refs) |
| Session/trace source | Every session archived | `sessions.py` → `.ronin/sessions/<id>.json` |

## 3. What is GENUINELY new in the spec

1. **The "volumes"** — behavioral law (Constitution / Tool Protocol / Approval
   Gates) written as machine-readable *training patterns*. Ronin has no such
   corpus. (The behaviors exist and are enforced; they are not written as a
   training source.)
2. **A volume → JSONL dataset builder.** The existing `finetune.py` builds from
   *sessions*, not from *authored protocol volumes*.
3. **On-device MLX LoRA / full training.** `finetune.py` deliberately emits a
   *rented-GPU* QLoRA kit; it does not train locally. Local MLX is a different
   trainer.
4. **Protocol-specific evals** (valid-tool-JSON %, schema compliance, gate respect,
   grounding, recovery, multi-turn stability). More behavior-specific than the
   existing task-outcome eval; partially new.
5. **Serving a Ronin-tuned adapter** as a local provider (adapter loading).

## 4. Design tensions the spec (as greenfield) does not address — READ THESE

- **Training a model to respect gates is redundant with enforcement.** Ronin's
  destructive floor + approval gate are enforced in the runtime regardless of what
  the model emits (hardened extensively this cycle). A fine-tune that "learns to
  ask before `rm -rf`" is a *nice-to-have*, not a safety mechanism — the safety is
  the gate, not the weights. The spec itself says "the model is not the product."
  So the honest value of the fine-tune is **offline/free protocol fluency**, not
  safety.
- **Local MLX training on 8 GB is not guaranteed.** Even a 1.5B LoRA is memory-
  tight; a 1.5B *full* fine-tune will very likely OOM (the spec concedes this).
  The existing `finetune.py` chose rented-GPU *for this reason*. The first sprint's
  win is the **mechanism** (volumes → dataset → evals → base-eval + a ready
  command), which does not depend on a training run succeeding here.
- **Two pipelines, one goal.** Building a parallel `training/` package risks
  duplicating `finetune.py`'s scrubbing/JSONL/dedup. The volume→JSONL builder should
  *reuse* `finetune.py`'s primitives (`to_jsonl`, the redactor) and `code_tools`'
  real schemas, not re-implement them.
- **Corpus source: volumes vs sessions.** The spec's volumes are authored,
  deterministic, license-clean (project-owned) — good. Sessions are richer but need
  scrubbing (already handled). Best is BOTH, but volumes-first is the right start
  (deterministic, no privacy surface).

## 5. Recommended first increment (the honest "mechanism")

Buildable and testable **without** a successful local train, and useful regardless:

1. **Volumes I–III** grounded in Ronin's *real* `code_tools` schemas and the *real*
   `approvals.py` gate model (accurate, not invented), each with machine-readable
   `training_patterns` / `jsonl_templates` / `eval_cases` / `anti_patterns`.
2. **A protocol-eval harness** that scores *any* provider on: valid-tool-JSON,
   schema compliance, gate respect, grounding, recovery, multi-turn stability —
   reusing `agent_eval`/`eval-suite` patterns. This is valuable immediately (it
   measures Claude, a local model, anything) with zero training.
3. **A volume → JSONL dataset builder** reusing `finetune.py`'s `to_jsonl` +
   redactor + `code_tools` schemas; validate every row against JSON Schema.
4. **The MLX LoRA command + `mlx_config`** wired and documented, base model scored —
   but the actual on-device train flagged as best-effort on 8 GB (may need bigger
   hardware, per the spec's own Page 6/30).

The trophy the spec names is "the adapter passing evals," but the *reusable* asset
is the **eval harness + volumes**, which improve Ronin's honesty measurement even if
the local train never fits this laptop.

## 6. Acceptance for Phase 0 (this report)

- [x] Discovery note names exact files (§2).
- [x] No implementation changes except `training/reports/`.
- [x] Overlap with existing `finetune`/`eval`/tool-schema/gate infrastructure made
      explicit, so Phase 1+ extends rather than duplicates.
