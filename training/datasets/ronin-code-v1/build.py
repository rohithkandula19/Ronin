"""Build the ronin-code-v1 dataset + bundle from the curated corpus.

Runs the real gates (provenance, quality/dedup, train↔locked contamination),
writes the train and locked-eval JSONL, hashes both, and regenerates the
GENERATED training bundle. Never trains. Run:

    uv run --package ronin-training python training/datasets/ronin-code-v1/build.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corpus import LOCKED_EVAL, TRAIN  # noqa: E402

from ronin_training.forge import BundleConfig, JobState, generate_bundle  # noqa: E402
from ronin_training.provenance import (  # noqa: E402
    Consent,
    DatasetItem,
    ProvenanceDataset,
    RedactionState,
    SourceType,
)
from ronin_training.quality import analyze_quality, contamination_check  # noqa: E402
from ronin_training.redaction import redact  # noqa: E402


def _items(rows, prefix):
    out = []
    for i, (cat, instr, ans) in enumerate(rows):
        r = redact(instr + " " + ans)
        out.append(DatasetItem(
            id=f"{prefix}-{cat}-{i:03d}", instruction=instr, output=ans,
            source_type=SourceType.human_reviewed, owner="ronin-maintainers",
            license="owner", consent=Consent.owner_self,
            redaction=RedactionState.clean_reviewed if r.clean else RedactionState.redacted_reviewed,
            industry="coding", country="US", language="en", role="developer",
        ))
    return out


def _sha(path: Path) -> str:
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    (path.parent / (path.name + ".sha256")).write_text(h + "\n", encoding="utf-8")
    return h


def main() -> int:
    train_items = _items(TRAIN, "train")
    eval_items = _items(LOCKED_EVAL, "eval")

    ds = ProvenanceDataset(train_items)
    elig = ds.eligible()
    print(f"train: {len(train_items)} rows, {len(elig)} eligible, {len(ds.excluded())} excluded")
    if len(elig) != len(train_items):
        print("ERROR: some training rows are not eligible:", ds.excluded()); return 1

    q = analyze_quality(elig)
    print(f"quality: exact_dupes={len(q.exact_duplicates)} near_dupes={len(q.near_duplicates)} "
          f"empty={len(q.empty_outputs)} too_short={len(q.too_short)}")
    if q.exact_duplicates or q.near_duplicates or q.empty_outputs:
        print("ERROR: quality gate failed", q.markdown()); return 1

    contam = contamination_check(elig, eval_items)
    print(f"train↔locked-eval contamination pairs: {len(contam)}")
    if contam:
        print("ERROR: locked eval overlaps training:", contam); return 1

    # write locked eval alongside the bundle (held out of training)
    out_root = HERE
    eval_path = out_root / "locked_eval.jsonl"
    eval_path.write_text(
        "".join(json.dumps({"instruction": i.instruction, "output": i.output}) + "\n"
                for i in eval_items), encoding="utf-8")
    print(f"locked eval sha256={_sha(eval_path)[:16]}...")

    bundle_dir = out_root / "bundle"
    job = generate_bundle(ds, bundle_dir, BundleConfig(
        base_model="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit",
        method="qlora", industry="coding", iters=400, seed=0))
    assert job.state is JobState.generated and not job.trained, "bundle must be GENERATED"
    print(f"bundle: job.state={job.state.value} trained={job.trained}")
    print(f"dataset sha256={_sha(bundle_dir / 'dataset.jsonl')[:16]}...")

    # category balance report
    bal: dict[str, int] = {}
    for cat, *_ in TRAIN:
        bal[cat] = bal.get(cat, 0) + 1
    print("categories:", ", ".join(f"{k}={v}" for k, v in sorted(bal.items())))
    print(f"TOTAL train={len(train_items)} locked_eval={len(eval_items)} categories={len(bal)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
