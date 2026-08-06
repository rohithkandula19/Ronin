# Root cause: `valid_tool_json` 0/4 — evidence report

Date: 2026-07-24 · branch `feat/ronin-model-v4` · investigator: automated evidence run
(no GPU used; every claim below reproduces offline).

## Verdict

**The pre-assumed train-vs-inference format mismatch is REFUTED.** The corpus →
chat-template → parser chain is byte-compatible end to end. The 0/4 is a
*behavioral/emission* failure under the eval's prompt conditions, with three real
(now-quantified) train/eval divergences as the actionable suspects — none of which
is a wire-format incompatibility.

## The experiment (reproducible, zero GPU)

1. Built the real dataset from the volumes:
   `uv run --package ronin-training python -m ronin_training.dataset_builder --volumes docs/volumes --out <tmp>` → 284 rows (228 train / 28 valid / 28 test); 211 of 228 train rows contain assistant `tool_calls`.
2. Fetched the **actual** chat template of the pinned model
   `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` (`tokenizer_config.json` →
   `chat_template`). It **does** carry a tools branch and renders assistant
   `tool_calls` as `\n<tool_call>\n{"name": "<name>", "arguments": <args|tojson>}\n</tool_call>`.
3. Rendered 50 tool-calling train rows through that template (jinja2), then fed the
   rendered text to **both** consumers:
   - runtime parser `parse_tool_calls` — `packages/cli/src/ronin_cli/embedded_provider.py:279-321`
   - eval extractor `extract_all_call_names` — `training/src/ronin_training/eval_runner.py:127-143`

   **Result: 50/50 rows get full credit from both.** Rendered training-format calls
   are exactly what both parsers accept.
4. Verified the train-time path renders identically: `mlx_lm.lora`'s `ChatDataset`
   passes each row's `tools` to `apply_chat_template` at both the pinned floor
   (mlx-lm 0.20.1, `mlx_lm/tuner/datasets.py:38-40`) and current (0.31.3,
   `datasets.py:59-63`). Same template, same rendering, train and eval.
5. Independent corroboration: the **bare base model** also scores 0/4
   (`training/reports/eval91_base.md:17`) with failures reading
   "did not call required tool" — an adapter-side format defect cannot explain the
   base model failing identically.

## What DOES diverge (quantified, file:line both sides)

| # | Divergence | Training side | Inference/eval side |
|---|---|---|---|
| D1 | **Tools-list distribution.** The model is never trained under the prompt it is evaluated under. | Rows carry only the tools they use: 0 tools ×18 rows, 1×92, 2×48, 3×41, 4×17, 5×11, 6×1 (from the generated `train.jsonl`; e.g. `docs/volumes/volume_02_tool_protocol.md:19` ships a single `read_file`) | Eval + runtime present **all 13** registry tools every time (`training/src/ronin_training/eval_runner.py:156-166`; runtime via `build_tools`) |
| D2 | **Argument typing.** Every training target teaches string-typed arguments, contradicting the template's own instruction the model reads at inference ("arguments": *args-json-object*). Parseable either way today (`embedded_provider.py:305-312` double-decodes), but it adds an escaped-quote nesting a 1.5B must learn, and trains systematic instruction-divergence. | `training/schemas/example.schema.json:23-42` **requires** `function.arguments` to be a string; **560/560** corpus tool-call arguments are string-typed | Template system block instructs `{"name": <function-name>, "arguments": <args-json-object>}`; eval regex `eval_runner.py:127` and runtime parser accept both |
| D3 | **No canonical renderer.** Three independent implementations merely *happen* to agree; nothing enforces it (the remediation proposed in `training/reports/v3_evidence_status.md:72-78` was never built). | corpus shape via `example.schema.json` + template rendering | `embedded_provider.py:279` regex; `eval_runner.py:127` regex |

## Implication for the model bet

The adapter's 0/4 is not a decoding bug to "fix in the parser" — the parsers accept
what training renders. The fixes that can actually move the metric are:
1. **Train under the eval/runtime prompt distribution** (all 13 tools presented,
   D1) so "choose the right tool out of 13 and emit the wrapper" is what SFT
   actually teaches.
2. **Canonicalize arguments as JSON objects** (D2) in newly generated rows, so
   targets obey the very instruction the prompt gives the model.
3. **One canonical renderer/parser module** (D3) shared by corpus generation,
   runtime, and eval — with golden round-trip tests so drift is structurally
   impossible. (Built in this branch: `packages/dialect` / `ronin_dialect`.)

None of this requires abandoning the kill criteria: 4/4 + >60/91 stands; the next
training cycle simply attacks the real gaps instead of a phantom format bug.

## Completion note

The source volumes were subsequently migrated to the canonical representation:
all 1,015 tool-call `arguments` values in the affected `ronin-sft` records are
now JSON objects rather than JSON-encoded strings. The migration is deliberately
limited to machine-readable SFT blocks; prose examples remain unchanged. The
strict dataset build and the training regression suite validate this invariant.
