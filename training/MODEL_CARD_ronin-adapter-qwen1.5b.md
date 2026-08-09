---
license: apache-2.0
base_model: Qwen/Qwen2.5-Coder-1.5B-Instruct
tags:
  - coding-agent
  - tool-use
  - lora
  - dpo
  - ronin
language:
  - en
library_name: peft
---

# ronin-adapter-qwen1.5b

A LoRA adapter that makes a 1.5B open model **ronin-native**: able to drive Ronin's
v2 runtime (`src/ronin`) in Ronin's own harness format — correct tool syntax, correct
approval-gate behaviour, correct recovery after a failure, correct planning shape.

> **STATUS: NOT TRAINED.** No training run has been performed. Every results cell
> below is `—`, meaning *not measured*. The pipeline, the configuration, the holdout
> split and the three-way evaluation harness exist and are tested; the weights do not.
> The environment this was built in has no GPU, and `mlx-lm` is Apple-silicon-only.
> See [What a real run needs](#what-a-real-training-run-needs) below.

## What it does

- **Tool syntax.** Emits `<ronin:tool_call>{"name": …, "arguments": {…}}</ronin:tool_call>`
  — the tag Ronin's format shim parses, with arguments as a JSON *object*, against the
  real 13-tool registry.
- **Gate behaviour.** Asks before a gated action instead of arguing with the gate, and
  treats a denial with feedback as a redirection rather than a dead end.
- **Recovery.** After a failed `ToolResult` or an approval denial, changes approach
  instead of re-issuing the identical call (the behaviour the runtime's stall detector
  exists to catch).
- **Planning shape.** Produces plans in the structure Ronin's harness consumes.

## What it does NOT do

**It does not add coding knowledge.** This is the most important line on the card and
it is not hedged. The adapter is trained on Ronin's *protocol*, not on code. A 1.5B
model that has learned the protocol perfectly is still a 1.5B model: it will not
out-reason a frontier model, will not solve a subtle multi-file bug that the base model
could not, and gains no new library or language knowledge from this fine-tune. Anyone
reading a protocol-compliance improvement as a capability improvement has misread it.

Also out of scope:

- **It is not a general assistant.** It is trained for one harness's format.
- **It does not make the runtime safe.** Ronin's destructive-command block and approval
  gates are the safety floor; the adapter is a behavioural nudge on top of them, and
  the model is not a security control.
- **It does not transfer to other agent harnesses.** Another framework's tool format is
  out of distribution — including Ronin's own **v1** dialect: `packages/dialect` uses a
  bare `<tool_call>` tag, this adapter uses `<ronin:tool_call>`. They are not
  interchangeable, and `ronin-code-1.5b` is a different model with a different card.

## Lineage and licence

| | |
|---|---|
| base model | [`Qwen/Qwen2.5-Coder-1.5B-Instruct`](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct) |
| base licence | **Apache-2.0** |
| MLX base (Apple silicon lane) | `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` |
| this adapter | Apache-2.0, inherited from the base |
| adapter type | LoRA, r=16, α=32, dropout 0.05 |
| adapted modules | `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj` |

An Apache-2.0 base is a deliberate choice, not a convenience: the adapter inherits the
base's licence, so a non-commercial base would make this adapter non-commercial too.
`ronin_training.adapter.config` records the licence and its validator errors if the
recorded licence disagrees with the base it has verified.

## Training data and its provenance

Produced by `ronin_training.harvest` (see `training/src/ronin_training/harvest/`), which
turns Ronin's own runtime traces into supervised examples and preference pairs.

- **Real session traces are opt-in.** Nothing is harvested from a user's sessions unless
  that user opted in. This card must not be read as a claim that any real session data
  is in the corpus — whether any is depends entirely on who opted in, and the harvest
  pipeline's own provenance record is the authority on what a given build contained.
- **No proprietary assistant output is used as a supervised target.** The corpus
  schema bans it at the licence-enum level (`ronin_training.validators`), and teacher
  distillation from closed-weight endpoints is blocked, because those terms of service
  forbid training a competing model on their outputs.
- **Synthetic and volume-authored rows** carry the same schema validation: every tool
  call in every row is checked against Ronin's real generated registry
  (`training/config/tool_registry.json`), so a row that calls a tool Ronin does not have
  or passes arguments its schema forbids is rejected rather than trained on.
- **Preference pairs** (`prompt` / `chosen` / `rejected`) drive the DPO pass and are
  concentrated on the two behaviours SFT alone under-teaches: adapting after a setback,
  and stopping at a gate.

### The holdout, and why it is by task

Ten percent of **tasks** are held out — never ten percent of examples. Two examples
harvested from the same task, one in train and one in the holdout, make the reported
score partly a memorisation score, and the inflation is invisible: the numbers look
better, the loss curve looks normal, and the adapter is worse than it reads.
`ronin_training.adapter.dataset.split_by_task` asserts disjointness on its own output,
writes a `split_manifest.json` with the two task lists and an explicit `leaked_tasks`
field, and refuses to write a split that leaks. Holding out 10% of tasks does not hold
out 10% of rows when tasks differ in size, so the manifest reports both fractions.

## Training procedure

Two passes, both LoRA, both `$0` on a free Colab/Kaggle T4 or Apple silicon.

| | pass 1 — SFT | pass 2 — DPO |
|---|---|---|
| config | `training/config/adapter_sft.yaml` | `training/config/adapter_dpo.yaml` |
| starts from | the base model | **the SFT checkpoint** |
| epochs | 3 (specified range: 2–3) | 1 |
| learning rate | 1e-5, cosine schedule, 3% warmup | 1e-5, cosine (carried from SFT) |
| sequence length | 4096 (documented 8192 opt-in, which requires gradient checkpointing) | 4096 |
| batch | 1 × grad-accum 4 | 1 × grad-accum 4 |
| β | — | 0.1 |
| seed | 0 | 0 |

Validate a config before spending a GPU-hour on it — no ML stack, no GPU needed:

```bash
uv run --package ronin-training python -m ronin_training.adapter
```

The DPO pass **must** name the SFT checkpoint in `resume_adapter`. DPO straight onto the
base model is a different experiment and the output directory looks identical either
way, so the validator refuses a DPO config without it.

## Evaluation methodology

Three targets, one suite, one scoring implementation: the **adapter**, the **base
Qwen2.5-Coder-1.5B** it was trained from, and **Kimi** as a reference ceiling. All three
run the same phase-11 task suite (`src/ronin/evals`) through the same runner; the
comparison would be worthless if the three columns were scored by two implementations,
so `ronin_training.adapter.threeway` calls the suite rather than carrying a copy of it.

Two metrics get first-class treatment because they are the adapter's actual thesis:

**tool-syntax validity** — of the assistant turns that *attempted* a tool call, the
fraction whose call parses out of a complete `<ronin:tool_call>` block, names a tool in
Ronin's registry, passes its arguments as a JSON object, and validates against that
tool's JSON schema. Turns that attempted no call are excluded from the denominator and
counted separately, so a model that never calls a tool cannot score 100%.

**recovery rate** — of the assistant turns immediately preceded by a *setback* (a failed
`ToolResult`, or an approval denial from the safety layer), the fraction that did not
repeat the setback's action. Repetition is measured with `ronin.core.loop.fingerprint`,
the same identity the runtime's stall detector uses. Calling no tool and reporting back
counts as recovery — it adapted. Re-issuing the identical fingerprint does not.

The suite's own pass rate is reported alongside them as context.

## Results

**Nothing here has been measured. `—` means not measured, and it is never rendered as
`0.000`.**

| metric | ronin-adapter-qwen1.5b | base Qwen2.5-Coder-1.5B | Kimi (ceiling) |
|---|---|---|---|
| tool-syntax validity | — | — | — |
| recovery rate | — | — | — |
| suite pass rate | — | — | — |
| turns observed | — | — | — |
| recovery opportunities | — | — | — |

Provenance, to be filled from the run that produces the numbers above:

| | |
|---|---|
| suite id / case count | — |
| adapter checkpoint | — |
| commit SHA | — |
| date | — |
| hardware | — |

### If the adapter loses, that is what gets published

The pre-committed decision rule: **the adapter must beat base Qwen on tool-syntax
validity *and* on recovery rate.** A tie counts as not beating it. If it loses on either,
the three-way report renders an "the adapter did NOT beat base" outcome with both deltas
and a concrete iterate list, and *that* is the artifact that ships. An honest negative
result is a better signal about a pipeline than a vague positive one, and the entire
reason this evaluation exists is to stop guessing — including guessing that the
fine-tune worked.

## Intended use

The default local model for Ronin's offline lane: no API key, no network egress.

```bash
export RONIN_ADAPTER=<adapter-dir>          # the checkpoint directory
export RONIN_LOCAL_BACKEND=mlx              # or hf on Linux/CUDA/CPU
ronin --model ronin-qwen-local "read pyproject.toml and tell me the package name"
```

The provider (`ronin.providers.local_adapter`) **fails closed**: an adapter path that is
set but missing or half-written raises rather than quietly serving the bare base model,
because a silently-base run produces a plausible number that is not a measurement of the
adapter. Both heavy backends are lazy imports that degrade with a named error saying what
to install.

## Out of scope

- Production code review, security review, or any decision where being wrong is
  expensive. Route those to a frontier model; Ronin's router exists for exactly that.
- Non-English instructions; non-code domains.
- Any harness other than Ronin v2, including Ronin v1's dialect.
- Running unattended with the approval gate disabled.

## Limitations, plainly

- **1.5B is small.** Protocol compliance is what moved; reasoning did not.
- **Inherits the base's failure modes**, hallucination under pressure included. The
  runtime's destructive-command block and approval gates are the mitigation, not the
  model.
- **The metrics are structural, not semantic.** Tool-syntax validity checks that a call
  is well-formed and schema-valid, *not* that it was the right call to make. Recovery
  rate checks that the model changed action, *not* that the new action was better. Both
  are deliberately deterministic; catching "was that a good idea?" needs a judge model,
  which would make the numbers non-reproducible.
- **Long transcripts are out of distribution** beyond the trained sequence length
  (4096 by default), even though the base model's context is longer.
- **The holdout is 10% of tasks from one harvest.** A high score on it is evidence about
  this corpus's task distribution, not about all of software engineering.

## What a real training run needs

Stated without hedging, because this environment cannot supply any of it:

1. **A GPU.** A free Colab or Kaggle T4 (16 GB) is enough for both passes at these
   settings, or an Apple-silicon Mac for the `mlx-lm` lane. There is no GPU here, and
   `$0` forbids renting one.
2. **`mlx-lm` on Apple silicon**, or `torch` + `transformers` + `peft` + `trl` on CUDA.
   Neither stack is installed here, and `mlx-lm` cannot be installed here at all.
3. **The harvested corpus**, built by `ronin_training.harvest`, split by task, with its
   `split_manifest.json` showing `leaked_tasks: []`.
4. **An API key for the Kimi column** of the three-way table. Without one the adapter and
   base columns still render and the verdict still resolves; the ceiling column is simply
   absent — not zero.
5. **Preference pairs** for the DPO pass. SFT can run without them; DPO cannot.

Until all of that has actually been run, every results cell on this card stays `—`.

## Reproducing

```bash
# 1. validate both configs (no GPU, no ML stack)
uv run --package ronin-training python -m ronin_training.adapter

# 2. see the pipeline and the honest-report rendering, offline
uv run --package ronin-training python -m ronin_training.adapter.demo

# 3. the tests that pin every hyperparameter and the no-leak split property
uv run pytest training/tests -q
```
