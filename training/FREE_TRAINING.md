# ronin-code-1.5b for $0 — the complete no-money pipeline

Every step below is free. No paid GPU, no paid API, no paid service — a Google
account (Colab) and a free Hugging Face account are the only requirements.
Kaggle (also free) is the fallback if a Colab T4 isn't available.

## The path

1. **Fork or clone** `https://github.com/rohithkandula19/Ronin` (public, MIT).
2. **Open the notebook in Colab:**
   `https://colab.research.google.com/github/rohithkandula19/Ronin/blob/main/training/colab/train_free.ipynb`
3. **Pick the free GPU:** `Runtime → Change runtime type → T4 GPU`.
4. **Run all cells, top to bottom:**
   - Cell 1 clones the repo and installs pinned deps.
   - Cell 2 regenerates the corpus **deterministically** (284 volume rows +
     5,000 synthetic rows, seed 42 — post D1/D2 fix: full 13-tool registry on
     every row, JSON-object arguments) and **verifies the frozen test-split
     hash** before a single training step. A tampered split refuses to train.
   - Cell 3 trains the QLoRA adapter (4-bit nf4, fp16, r=16, α=32, seq ≤ 2048,
     seed 42), checkpointing every 100 steps. **Disconnected?** Re-run
     Cells 1–2, add `--resume` to Cell 3, run it again — at most ~100 steps lost.
   - Cell 4 copies the adapter to your Google Drive so it survives the session,
     and prints the `hf upload` command for your free HF account.
   - Cell 5 runs the **honest eval**: the frozen, sha256-pinned 91-case set,
     with `--baseline` so your adapter's score prints next to bare
     Qwen-1.5B's. The report (score/91, valid_tool_json/N, per-task PASS/FAIL,
     model + adapter + commit SHA) lands in `training/reports/`.
5. **Read the score against the pre-committed gate:**
   **valid_tool_json 4/4 AND >60/91.**
   - Clears it → publish with your free HF account:
     `hf upload <you>/ronin-code-1.5b <adapter-dir> --repo-type model`
     and fill the numbers into `training/MODEL_CARD_ronin-code-1.5b.md`.
   - Misses it → the honest outcome is archiving the checkpoint with its
     report. No massaged numbers, ever.

## Weak checkpoint ≠ broken runtime

Independent of training quality, the runtime grammar lock makes malformed tool
JSON **unsamplable** on the llama.cpp engine:

```bash
RONIN_GRAMMAR=1   # default-on; generated from packages/dialect (the canonical spec)
RONIN_GRAMMAR=0   # opt out
```

Tags, name-first key order, registry-locked tool names, and JSON-object
arguments are enforced token-by-token at decode time
(`packages/dialect/src/ronin_dialect/gbnf.py`). The MLX engine has no grammar
support — there the trained dialect carries the load alone (stated, not faked).

## Free-tier facts (so nothing surprises you)

| constraint | reality | mitigation |
|---|---|---|
| Colab session timeout | a few hours, usage-dependent | 100-step checkpoints + `--resume` |
| random disconnects | happen | same |
| T4 availability | not guaranteed on free tier | off-peak hours; Kaggle fallback |
| Kaggle quota | ~30 GPU-hrs/week free | one training run fits comfortably |
| wall-clock / score claims | **none made here** | your run's report is the only truth |
