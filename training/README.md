# ronin-training

Turn Ronin's **behavioral volumes** into a validated training dataset, protocol
evals, and an on-device MLX fine-tuning command. The pipeline is deterministic and
honest: it never fabricates a training row, an eval result, or a metric.

```
docs/volumes/*.md   ──parse──▶  ronin-sft / ronin-eval blocks
                    ──validate──▶ against JSON schema + REAL tool registry
                    ──dedup/split──▶ train / valid / test .jsonl  (MLX format)
                                     evals/ronin_protocol_eval.jsonl
                                     reports/dataset_report.md
```

## Why volumes

A "volume" is a Markdown file under [`docs/volumes/`](../docs/volumes) whose
human-readable law is interleaved with machine-readable fenced blocks:

- ` ```ronin-sft ` — one supervised training row (system/user/assistant/tool
  messages, optional `tools`).
- ` ```ronin-eval ` — one deterministic protocol check
  (`must_include` / `must_not_include` / `must_call_tools` / `must_not_call_tools`).

Editing behavior means editing the law and its examples in the same place. Six
volumes ship today (behavioral constitution, tool protocol, approval gates, recovery,
grounding, breadth) with 55 training rows and 19 eval cases across 22 families.

## Honesty invariants (enforced, not aspirational)

- **Every tool call is checked against the real registry.** `config/tool_registry.json`
  is generated from Ronin's live `build_code_tools` + `build_background_tools`; a row
  that calls a tool Ronin doesn't have, or passes arguments its schema forbids, is
  rejected. `python -m ronin_training.tool_registry_loader --check` fails CI on drift.
- **Proprietary assistant output is banned as an SFT target** (license enum forbids it).
- **Malformed blocks are reported, never silently dropped** (the builder refuses the
  build in strict mode and names the file + block).
- **Safety families have coverage floors.** `config/scenario_weights.yaml` declares a
  minimum example count per family; the build report flags any family below floor.
- **No fabricated eval numbers.** The eval runner scores a *provider you supply*. With
  no model wired, there is no score — it will not invent one.

## Build the dataset

```bash
uv run --package ronin-training python -m ronin_training.dataset_builder \
    --volumes docs/volumes --out training/data/generated
```

Writes `train.jsonl` / `valid.jsonl` / `test.jsonl` (each row is just `messages` +
optional `tools`, the shape MLX-LM consumes), the protocol eval set, and
`reports/dataset_report.md`. Strict by default: any invalid row aborts the build.
Pass `--allow-invalid` to exclude bad rows instead of failing.

## Evaluate a model on the protocol

**Runs free.** Two provider paths: `--provider mlx` (Apple Silicon, on-device)
and `--provider hf` (transformers + optional PEFT adapter) — the second runs on
a free Colab/Kaggle T4 (same notebook as training, Cell 5) or any machine that
can load the 1.5B base model, CPU included. The frozen 91-case set is
sha256-pinned; the runner refuses a drifted set and stamps model+adapter+commit
into every report. `--baseline` prints the adapter score next to
"Qwen-1.5B alone" — that delta is the value proposition.


```python
from ronin_training.eval_runner import load_cases, run_evals, mlx_provider, write_report

cases = load_cases("training/data/evals/ronin_protocol_eval.jsonl")
provider = mlx_provider("mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit")  # base model
report = run_evals(cases, provider)
write_report(report, "training/reports/base_eval.md", title="Base model — protocol")
```

Measure the **base** model first. Only after you have base numbers and adapter numbers
from the *same* eval set can you claim a fine-tune changed anything.

### What these evals can and cannot catch

The scorer is deterministic substring/tool-name matching, hardened by a 70-agent
adversarial audit of every case (contraction/apostrophe normalization, claim-shaped
banned phrases instead of bare tokens, call-shaped tool extraction, `must_call_any_of`
OR-groups, hallucinated-tool detection via `forbid_unknown_tools`). Known structural
limits, on purpose rather than papered over:

- **Tool arguments are not semantically inspected.** A case can require `run_command`
  be called but not that its `command` is the right one.
- **Hedged fake success can slip past substring bans.** Catching every phrasing of a
  dishonest claim needs a judge model, which would make the eval nondeterministic;
  these are protocol smoke checks, not a semantic grader.

Treat pass rates as comparable between checkpoints on the same eval set — not as an
absolute measure of agent quality.

## Fine-tune on-device (Apple Silicon)

```python
from ronin_training.mlx_config import MLXTrainConfig
cfg = MLXTrainConfig(fine_tune_type="lora")      # QLoRA via a 4-bit base model
print(cfg.command_str())                          # the exact mlx_lm.lora argv to run
for w in cfg.memory_warnings():                   # honest RAM heuristics
    print("!", w)
```

Install the training extra and run the command it prints:

```bash
uv pip install 'mlx-lm>=0.20'      # Apple Silicon only
python -m mlx_lm.lora --model ... --train --data training/data/generated ...
```

**Reality check for 8 GB machines:** QLoRA over a 4-bit 1.5B model is the default
because it *fits*. The full fine-tune lane (`fine_tune_type="full"`) and non-quantized
LoRA are real options the config exposes, but they will likely OOM on an 8 GB M2 —
`memory_warnings()` says so, and the 0.5B model is the fallback. Nothing here claims a
successful train you didn't run.

## Run Ronin WITH the fine-tuned adapter

The embedded provider loads a trained LoRA adapter on top of the 4-bit base model
(mlx engine / Apple Silicon only). The recommended checkpoint is **v2 iter-150**
(best protocol-eval score, 36/91 — full checkpoint sweep and the reasoning in
`reports/model_comparison.md`; note protocol evals peak at iter-150 even though
val loss bottoms at iter-300).

```bash
# point Ronin's local provider at the adapter (env var), then use provider "local"
export RONIN_ADAPTER=training/adapters/ronin_1.5b_v2_ck/i150
ronin --provider local "read pyproject.toml and tell me the package name"
```

or programmatically:

```python
from ronin_cli.embedded_provider import EmbeddedProvider
provider = EmbeddedProvider(
    model="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit",
    adapter_path="training/adapters/ronin_1.5b_iter150",
)
```

Adapters are **not committed** (binary); reproduce from seed 0 with the commands in
`reports/finetune_comparison.md`. The provider fails loudly — a missing adapter dir
raises instead of silently serving the base model, and the llama-cpp engine rejects
`adapter_path` outright. Tool calls are parsed ONLY from well-formed
`<tool_call>` blocks; a call the model merely quotes as bare JSON in prose is never
executed (that strictness is a safety property, verified by a live probe where a
model "refused" `rm -rf ~` while echoing the call JSON in its refusal text).

Live smoke test (opt-in, loads real weights):

```bash
RONIN_ADAPTER_SMOKE=1 uv run pytest packages/cli/tests/test_adapter_smoke_live.py -q
```

## Layout

| path | what |
|---|---|
| `docs/volumes/volume_*.md` | the source corpus (law + `ronin-sft`/`ronin-eval` blocks) |
| `config/tool_registry.json` | generated ground-truth tool schemas (do not hand-edit) |
| `config/scenario_weights.yaml` | per-family coverage floors |
| `schemas/*.schema.json` | JSON Schemas for rows / evals / registry |
| `src/ronin_training/volume_parser.py` | fenced-block extractor (pure) |
| `src/ronin_training/validators.py` | schema + registry + license validation |
| `src/ronin_training/split_dataset.py` | deterministic family-stratified split |
| `src/ronin_training/dataset_builder.py` | the orchestrator + report |
| `src/ronin_training/coverage.py` | coverage-floor checker |
| `src/ronin_training/eval_runner.py` | provider-agnostic protocol eval scorer |
| `src/ronin_training/mlx_config.py` | MLX training command builder |
| `src/ronin_training/tool_registry_loader.py` | regenerate/check the registry |

## Test

```bash
uv run pytest training -q
```
