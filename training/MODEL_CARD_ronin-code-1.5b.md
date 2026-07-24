---
license: apache-2.0
base_model: Qwen/Qwen2.5-Coder-1.5B-Instruct
tags:
  - code
  - coding-agent
  - tool-use
  - lora
  - ronin
language:
  - en
library_name: peft
---

# ronin-code-1.5b

The embedded brain for [Ronin](https://github.com/rohithkandula19/Ronin) — the
coding agent that runs **free, local, and air-gapped**. A LoRA fine-tune of
Qwen2.5-Coder-1.5B-Instruct on Ronin's tool-call dialect and behavioral
protocol, so a 1.5B model that fits on a laptop can drive Ronin's runtime
(read → edit → run → verify) with no API key and zero network egress.

## Lineage & license

- **Base:** [`Qwen/Qwen2.5-Coder-1.5B-Instruct`](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct)
  (1.54B params, **Apache-2.0** — license verified against the repository
  metadata at card-writing time).
- **This adapter:** Apache-2.0, same as the base.
- **Training data lineage:** entirely project-owned — no proprietary-assistant
  outputs (banned at the schema level), no Anthropic/OpenAI-derived teacher
  traces (their ToS forbid training competing models on their outputs; the
  corpus generator's teacher mode is hard-blocked for non-open-weight
  endpoints).

## Training data

- 284 hand-authored behavioral rows from Ronin's volumes (tool protocol,
  approval gates, recovery, grounding, multi-turn discipline).
- 5,000 synthetic dialect rows from `ronin_training.synthetic_corpus`
  (deterministic, seed 42): 11 task families over Ronin's real 13-tool
  registry, every call schema-validated, canonical JSON-object arguments,
  full tool registry attached to every row — the exact prompt distribution
  the runtime presents.
- Frozen, hash-pinned test split never trained on
  (`training/data/synthetic/test.jsonl.sha256`).

## Evaluation — honest numbers only

Scores come from Ronin's frozen 91-task protocol eval (sha256-pinned set;
runner refuses a drifted set; every report stamps model, adapter, commit SHA).
**A number that wasn't measured never appears here.**

| metric | Qwen-1.5B alone (baseline) | ronin-code-1.5b | delta |
|---|---|---|---|
| protocol eval (of 91) | PENDING — no shippable checkpoint yet | PENDING | PENDING |
| valid_tool_json (of 4) | PENDING | PENDING | PENDING |

Ship gate (pre-committed): this card gets numbers only from a checkpoint that
scores **valid_tool_json 4/4 AND >60/91**. If no checkpoint clears the gate,
the honest outcome is archiving this effort, not massaging a number.

## Intended use

The default embedded model for Ronin's offline wedge: `ronin init` → provider
`local`, `RONIN_ADAPTER=<adapter dir>`. The provider fails closed — a missing
or incomplete adapter raises rather than silently serving the bare base model.

## Limitations — plainly

- **1.5B is small.** It follows Ronin's protocol; it does not out-reason
  frontier models. Complex multi-file refactors and subtle debugging remain
  frontier-model territory — Ronin routes there when you configure a key.
- English-centric; code-centric.
- Trained for Ronin's 13-tool runtime; other harnesses' tool formats are out
  of distribution.
- Inherits base-model failure modes (hallucination under pressure); Ronin's
  runtime floor (destructive-command block, approval gates) is the mitigation,
  not the model.

## Upload (BLOCKED until the gate clears + HF credentials exist)

```bash
hf upload ronin/ronin-code-1.5b training/adapters/ronin-code-1.5b-v4/adapters \
  --repo-type model
```
