"""The code-enforced ship gate for ronin-code-1.5b.

`publish_model.sh` may only reach `hf upload` through this module: `check`
reads the pre-committed criteria from ``training/config/publish_gate.json``
(never hardcoded, never operator-supplied) and exits non-zero with the exact
reasons when a measured run misses them. `fill-card` writes the model card's
PENDING cells from the same measured scores file, stamped with the run's
commit SHA and eval-set hash — a number that wasn't measured never appears.
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GATE = _ROOT / "training" / "config" / "publish_gate.json"


def load_gate(path: str | Path | None = None) -> dict:
    return json.loads(Path(path or DEFAULT_GATE).read_text(encoding="utf-8"))


def evaluate_gate(run: dict, gate: dict) -> list[str]:
    """[] iff ``run`` (one entry of the eval runner's --scores-json payload:
    passed/total/by_category) clears every criterion. Reasons otherwise."""
    errs: list[str] = []
    passed, total = run.get("passed", 0), run.get("total", 0)
    expected = gate.get("expected_total")
    if expected is not None and total != expected:
        errs.append(f"ran {total} cases, the frozen set has {expected} — "
                    "not a full-set run, not comparable")
    cats = {k: tuple(v) for k, v in (run.get("by_category") or {}).items()}
    for cat in gate.get("require_all_pass_categories", []):
        p, t = cats.get(cat, (0, 0))
        if t == 0:
            errs.append(f"category {cat!r} absent from the run — the gate "
                        "cannot be verified, so it is not cleared")
        elif p != t:
            errs.append(f"{cat} {p}/{t} — gate requires {t}/{t}")
    floor = gate.get("min_passed_exclusive")
    if floor is not None and passed <= floor:
        errs.append(f"passed {passed}/{total} — gate requires more than {floor}")
    return errs


def _fmt_cat(run: dict, cat: str) -> str:
    p, t = (run.get("by_category") or {}).get(cat, (0, 0))
    return f"{p}/{t}"


def fill_model_card(card_text: str, scores: dict, *, adapter_path: str = "") -> str:
    """Replace the card's PENDING result rows with the measured numbers and a
    provenance stamp. ``scores`` is the eval runner's --scores-json payload;
    the adapter run is required, the baseline run fills the delta column."""
    adapter = scores["adapter"]
    base = scores.get("baseline")
    prov = adapter.get("provenance") or {}

    def row(metric: str, a_val: str, b_val: str, delta: str) -> str:
        return f"| {metric} | {b_val} | {a_val} | {delta} |"

    a_pass, total = adapter["passed"], adapter["total"]
    if base:
        overall = row(f"protocol eval (of {total})", str(a_pass),
                      str(base["passed"]), f"{a_pass - base['passed']:+d}")
        vtj = row("valid_tool_json (of 4)", _fmt_cat(adapter, "valid_tool_json"),
                  _fmt_cat(base, "valid_tool_json"), "measured")
    else:
        overall = row(f"protocol eval (of {total})", str(a_pass),
                      "(baseline not run)", "—")
        vtj = row("valid_tool_json (of 4)", _fmt_cat(adapter, "valid_tool_json"),
                  "(baseline not run)", "—")

    lines = card_text.splitlines()
    out = []
    for line in lines:
        if line.startswith("| protocol eval") and "PENDING" in line:
            out.append(overall)
        elif line.startswith("| valid_tool_json") and "PENDING" in line:
            out.append(vtj)
        else:
            out.append(line)
    stamp = (f"\nMeasured: commit `{prov.get('commit', 'unknown')}`, eval set "
             f"sha256 `{prov.get('eval_set_sha256', 'unknown')}`, "
             f"{prov.get('timestamp', '?')}"
             + (f", adapter `{adapter_path}`" if adapter_path else "") + ".\n")
    text = "\n".join(out)
    if "PENDING" in " ".join(l for l in out if l.startswith("|")):
        raise ValueError("model card still has PENDING result cells after fill")
    # stamp goes right under the results table (after the vtj row)
    return text.replace(vtj, vtj + "\n" + stamp.strip("\n"), 1) + ("\n" if not text.endswith("\n") else "")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="exit 1 unless the measured run clears the gate")
    chk.add_argument("--scores", required=True, help="--scores-json file from the eval runner")
    chk.add_argument("--gate", default=None, help="gate config (default: the committed pin)")
    chk.add_argument("--run", default="adapter", help="which run in the scores file to gate")
    fc = sub.add_parser("fill-card", help="write the card's PENDING cells from measured scores")
    fc.add_argument("--scores", required=True)
    fc.add_argument("--gate", default=None)
    fc.add_argument("--card", default=None, help="card path (default: from gate config)")
    fc.add_argument("--adapter-path", default="")
    a = ap.parse_args(argv)

    gate = load_gate(a.gate)
    scores = json.loads(Path(a.scores).read_text(encoding="utf-8"))

    if a.cmd == "check":
        run = scores.get(a.run)
        if run is None:
            print(f"PUBLISH REFUSED: no {a.run!r} run in {a.scores}")
            return 1
        errs = evaluate_gate(run, gate)
        if errs:
            print("PUBLISH REFUSED — the gate is not cleared:")
            for e in errs:
                print(f"- {e}")
            return 1
        print(f"gate cleared: {run['passed']}/{run['total']}, "
              f"valid_tool_json {_fmt_cat(run, 'valid_tool_json')}")
        return 0

    # fill-card — refuses on its own too: never stamp a card with a sub-gate run
    errs = evaluate_gate(scores.get("adapter") or {}, gate)
    if errs:
        print("CARD NOT WRITTEN — the gate is not cleared:")
        for e in errs:
            print(f"- {e}")
        return 1
    card = Path(a.card or (_ROOT / gate["model_card"]))
    card.write_text(fill_model_card(card.read_text(encoding="utf-8"), scores,
                                    adapter_path=a.adapter_path), encoding="utf-8")
    print(f"model card filled: {card}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
