# Free training on Google Colab — $0, no paid anything

`train_free.ipynb` trains the ronin-code-1.5b QLoRA adapter on a **free Colab
T4**. No paid GPU, no paid API, no paid service anywhere in the path.

## Steps

1. Open the notebook in Colab:
   `https://colab.research.google.com/github/rohithkandula19/Ronin/blob/main/training/colab/train_free.ipynb`
2. `Runtime → Change runtime type → T4 GPU`.
3. Run the cells top to bottom. Cell 2 hash-verifies the frozen test split
   before any training; Cell 5 runs the honest eval with the baseline delta.

## Free-tier limits, stated plainly

- **Sessions time out** (usage-dependent, typically a few hours) and **can
  disconnect at any moment**. The trainer checkpoints every 100 steps —
  after a disconnect, re-run Cells 1–2 and add `--resume` to Cell 3; you lose
  at most ~100 steps.
- **T4 availability is not guaranteed** on the free tier; off-peak hours help.
- Disk and RAM are capped; the notebook's QLoRA config (4-bit nf4, fp16,
  seq ≤ 2048) is sized for the T4's 16 GB.
- **No wall-clock or score is claimed here.** Both come from your actual run —
  the eval writes a provenance-stamped report; the pre-committed gate is
  **valid_tool_json 4/4 AND >60/91**, or archive.

## Fallback: Kaggle (also free)

Kaggle gives ~30 GPU hours/week free (T4/P100). Create a Notebook, enable the
GPU accelerator, and run the same cells verbatim (skip the `google.colab`
Drive mount in Cell 4 — Kaggle persists `/kaggle/working` as the notebook
output instead).

## Weak checkpoint? The grammar still holds the line

Even if a checkpoint misses the gate, the runtime's GBNF grammar lock
(`RONIN_GRAMMAR=1`, default-on on the llama.cpp engine) makes malformed tool
JSON unsamplable at decode time — see `packages/dialect/src/ronin_dialect/gbnf.py`.
