"""``python -m ronin_training.dpo.train`` — the DPO training entrypoint.

Loads a DPO profile (:class:`ronin_training.dpo.profile.DPOProfile`), **validates the
preference set offline** — every row must be prefix-identical (one shared prompt, one
rejected and one chosen assistant turn, and they differ) and the classes must be balanced —
then drives trl's ``DPOTrainer`` from the SFT adapter with the SFT model as the frozen
reference.

``--check`` is the offline half this environment runs: it does the whole validation and
prints the recipe **without importing torch/trl**, the same "safe anywhere" contract the
SFT entrypoint keeps. A row whose prefixes differ, or a chosen turn identical to its
rejected one, fails here — before any GPU time — because that is the pair that would train
DPO on the wrong signal. The trl import lives past the gate, so a box without it gets a
clean "install …" message, not a traceback.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ronin_training.adapter.dataset import read_jsonl
from ronin_training.dpo.pairs import PreferenceClass
from ronin_training.dpo.profile import DPOProfile


class DPOTrainError(RuntimeError):
    """A DPO run that cannot proceed, with the reason and (where possible) the fix."""


@dataclass(frozen=True, slots=True)
class DPOCheckReport:
    """What ``--check`` established: a validated, balanced preference set and the recipe."""

    profile_name: str
    pairs: int
    class_counts: Mapping[str, int]
    balanced: bool
    recipe: tuple[str, ...]

    def render(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self.class_counts.items()) if v)
        return "\n".join(
            [
                f"dpo check: profile={self.profile_name}",
                f"  pairs: {self.pairs}  balanced={self.balanced}",
                f"  by class: {counts or '(none)'}",
                f"  would run: {' '.join(self.recipe)}",
            ]
        )


def _validate_row(row: Mapping[str, Any], index: int) -> str:
    """Check one trl DPO row is prefix-identical; return its pref_class or raise."""
    prompt = row.get("prompt")
    chosen = row.get("chosen")
    rejected = row.get("rejected")
    if not isinstance(prompt, list) or not prompt:
        raise DPOTrainError(f"row {index}: missing a non-empty shared 'prompt'")
    for name, side in (("chosen", chosen), ("rejected", rejected)):
        if not isinstance(side, list) or len(side) != 1:
            raise DPOTrainError(f"row {index}: '{name}' must be exactly one assistant turn")
        if str(side[0].get("role", "")) != "assistant":
            raise DPOTrainError(f"row {index}: '{name}' turn must have role 'assistant'")
    if chosen == rejected:
        raise DPOTrainError(f"row {index}: chosen and rejected are identical — it teaches nothing")
    return str(row.get("pref_class", ""))


def check_run(
    profile: DPOProfile,
    rows: Sequence[Mapping[str, Any]],
    *,
    config_path: str,
    data_dir: str,
    out_dir: str,
    require_balanced: bool = False,
) -> DPOCheckReport:
    """The offline gate: validate the profile + every pair's prefix-identity, then the recipe.

    ``require_balanced`` (off by default) additionally refuses a set where the present
    classes are not equal in count — useful as a stricter pre-GPU check once the pool is big
    enough to balance.
    """
    profile.validate()
    if not rows:
        raise DPOTrainError("no preference pairs to train on")
    counts = {str(c): 0 for c in PreferenceClass}
    for index, row in enumerate(rows):
        pref = _validate_row(row, index)
        if pref in counts:
            counts[pref] += 1
    present = [v for v in counts.values() if v]
    balanced = len(set(present)) <= 1
    if require_balanced and not balanced:
        raise DPOTrainError(f"preference classes are not balanced: {counts}")
    recipe = profile.run_command(config_path, data_dir, out_dir)
    return DPOCheckReport(
        profile_name=profile.name,
        pairs=len(rows),
        class_counts=counts,
        balanced=balanced,
        recipe=tuple(recipe),
    )


def _run_real(profile: DPOProfile) -> int:  # pragma: no cover - needs a GPU box with trl
    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
        from trl import DPOConfig, DPOTrainer  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise DPOTrainError(
            "the DPO pass needs a GPU box with torch + trl: pip install 'ronin-training[cuda]', "
            "then re-run. --check is the offline gate."
        ) from exc
    raise DPOTrainError(
        f"wire this profile into trl.DPOTrainer on the GPU box: init + frozen ref = "
        f"{profile.ref_adapter}, beta={profile.beta}, loss={profile.loss}, lr={profile.learning_rate:g}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ronin_training.dpo.train", description=__doc__)
    parser.add_argument("--config", required=True, help="a DPO profile yaml")
    parser.add_argument("--data", required=True, help="preference pairs .jsonl (trl conversational)")
    parser.add_argument("--out", default="training/adapters/dpo", help="adapter output dir")
    parser.add_argument("--lane", default="", choices=["", "peft", "mlx"], help="override the lane")
    parser.add_argument("--require-balanced", action="store_true", help="refuse an unbalanced set")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the pairs offline and print the recipe; imports no training stack",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        profile = DPOProfile.load(args.config)
        if args.lane:
            from dataclasses import replace

            profile = replace(profile, lane=args.lane)
        rows = list(read_jsonl(args.data))
        report = check_run(
            profile,
            rows,
            config_path=args.config,
            data_dir=args.data,
            out_dir=args.out,
            require_balanced=args.require_balanced,
        )
    except (DPOTrainError, ValueError, OSError) as exc:
        print(f"dpo train: {exc}", file=sys.stderr)
        return 1

    print(report.render())
    if args.check:
        return 0
    try:
        return _run_real(profile)
    except DPOTrainError as exc:
        print(f"dpo train: {exc}", file=sys.stderr)
        return 1


__all__ = ["DPOCheckReport", "DPOTrainError", "check_run", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
