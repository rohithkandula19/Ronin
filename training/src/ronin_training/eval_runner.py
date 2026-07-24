"""Run Ronin's protocol eval cases against a provider and score them honestly.

The runner is provider-agnostic: you hand it a callable that turns an eval case into
a `ProviderResponse` (the model's text plus the tool names it tried to call), and it
checks the case's `must_include` / `must_not_include` / `must_call_tools` /
`must_not_call_tools` assertions. It computes nothing on its own — if no provider
runs, there is no score. That is deliberate: the spec forbids reporting an eval
result you did not actually produce (measure the base model before claiming a
fine-tune improved anything).

Wiring a real model is the caller's job. `mlx_provider()` is an optional adapter for
an on-device MLX model; it imports `mlx_lm` lazily and raises a clear error if it
isn't installed, rather than silently returning empty responses that would inflate
the pass rate.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable


@dataclass
class ProviderResponse:
    """What a model produced for one eval case."""
    text: str = ""
    tool_calls: list[str] = field(default_factory=list)   # tool names, in call order


Provider = Callable[[dict], ProviderResponse]


@dataclass
class CaseResult:
    eval_id: str
    category: str
    passed: bool
    failures: list[str] = field(default_factory=list)


def _normalize(s: str) -> str:
    """Casefolded text with typographic apostrophes straightened and contractions
    expanded (n't → not), so \"haven't run\" satisfies a required \"not run\" and a
    curly-quoted \"we’re good\" still matches a straight-quoted ban. Markers are
    semantic, not literal — an audit showed correct answers failing on contraction
    variance alone."""
    s = s.replace("’", "'")
    s = re.sub(r"n't\b", " not", s, flags=re.IGNORECASE)
    return s.casefold()


def score_case(case: dict, resp: ProviderResponse) -> CaseResult:
    """Check one response against one case's assertions. Text checks are normalized
    (case, apostrophes, contractions); tool checks are exact names."""
    text = _normalize(resp.text)
    called = list(resp.tool_calls)
    failures: list[str] = []

    for needle in case.get("must_include") or []:
        if _normalize(needle) not in text:
            failures.append(f"missing required phrase {needle!r}")
    for needle in case.get("must_not_include") or []:
        if _normalize(needle) in text:
            failures.append(f"contains banned phrase {needle!r}")
    for tool in case.get("must_call_tools") or []:
        if tool not in called:
            failures.append(f"did not call required tool {tool!r}")
    any_of = case.get("must_call_any_of") or []
    if any_of and not any(t in called for t in any_of):
        failures.append(f"called none of the acceptable tools {any_of!r}")
    for tool in case.get("must_not_call_tools") or []:
        if tool in called:
            failures.append(f"called forbidden tool {tool!r}")
    if case.get("forbid_unknown_tools"):
        unknown = [n for n in extract_all_call_names(resp.text)
                   if n not in _registry_names()]
        if unknown:
            failures.append(f"called hallucinated tool(s) {unknown!r}")

    return CaseResult(case["eval_id"], case.get("category", "?"), not failures, failures)


@dataclass
class EvalReport:
    total: int
    passed: int
    results: list[CaseResult]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def by_category(self) -> dict[str, tuple[int, int]]:
        """category -> (passed, total)."""
        agg: dict[str, list[int]] = {}
        for r in self.results:
            cell = agg.setdefault(r.category, [0, 0])
            cell[0] += int(r.passed)
            cell[1] += 1
        return {k: (v[0], v[1]) for k, v in sorted(agg.items())}


def load_cases(path: str | Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def run_evals(cases: list[dict], provider: Provider) -> EvalReport:
    results = [score_case(c, provider(c)) for c in cases]
    return EvalReport(len(results), sum(r.passed for r in results), results)


# ------------------------------------------------------------------ tool-call parsing

# Ronin's tool calls surface as JSON objects in the model's output. The eval only
# needs the tool NAMES that were requested, so extract them without executing.
#
# RUNTIME PARITY: extraction mirrors the EmbeddedProvider's parser exactly — a
# call counts ONLY inside a ``<tool_call>...</tool_call>`` block AND in call shape
# (a name key followed by an arguments key). Ronin's runtime refuses to execute a
# call the model merely quoted as bare JSON in prose (a live probe showed a model
# "refusing" rm -rf ~ while echoing the call JSON in its refusal — executing that
# would be catastrophic), so the eval must not award credit for output the runtime
# would not consume.
# The call-shape regex and extraction now live in the canonical ronin_dialect
# module — the SAME code the runtime parser and corpus renderer use, so the eval
# can never drift from what the runtime executes (the guarantee this comment
# used to merely assert). _TOOLCALL kept as an alias for back-compat.
from ronin_dialect import CALL_SHAPE_RE as _TOOLCALL  # noqa: E402
from ronin_dialect import extract_call_names as _dialect_extract  # noqa: E402


def extract_all_call_names(raw: str) -> list[str]:
    """Every wrapper-enclosed, call-shaped tool name in the output, INCLUDING names
    not in Ronin's registry — used to detect hallucinated tools. Delegates to the
    canonical ``ronin_dialect`` extractor."""
    return _dialect_extract(raw)


def extract_tool_names(raw: str) -> list[str]:
    """Call-shaped tool names restricted to Ronin's real registry (the names the
    tool assertions check against)."""
    real = set(_registry_names())
    return [n for n in extract_all_call_names(raw) if n in real]


@lru_cache(maxsize=1)
def _registry() -> dict:
    reg = Path(__file__).resolve().parents[2] / "config" / "tool_registry.json"
    return json.loads(reg.read_text(encoding="utf-8"))


def _registry_names() -> tuple[str, ...]:
    return tuple(t["name"] for t in _registry()["tools"])


def ronin_tools_for_template() -> list[dict]:
    """Ronin's real tools in the OpenAI function format a chat template expects.

    Ronin always runs with these available, so an honest protocol eval must present
    them too — otherwise the model has no way to emit the tool calls the eval checks."""
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"],
        }}
        for t in _registry()["tools"]
    ]


# ------------------------------------------------------------------ optional MLX adapter

def mlx_provider(model_path: str, *, adapter_path: str | None = None,
                 max_tokens: int = 512) -> Provider:
    """Return a Provider backed by an on-device MLX model. Raises ImportError with a
    clear message if `mlx_lm` isn't installed — never degrades to empty responses."""
    try:
        from mlx_lm import generate, load          # type: ignore
    except ImportError as e:                        # pragma: no cover - env-dependent
        raise ImportError(
            "mlx-lm is required for mlx_provider. Install with "
            "`pip install 'ronin-training[mlx]'` on an Apple Silicon Mac."
        ) from e

    model, tokenizer = load(model_path, adapter_path=adapter_path)  # pragma: no cover
    tools = ronin_tools_for_template()

    def _provider(case: dict) -> ProviderResponse:  # pragma: no cover - needs a model
        text = generate(
            model, tokenizer,
            prompt=tokenizer.apply_chat_template(
                case_messages(case), tools=tools, add_generation_prompt=True),
            max_tokens=max_tokens, verbose=False,
        )
        return ProviderResponse(text=text, tool_calls=extract_tool_names(text))

    return _provider


def hf_provider(model_path: str, *, adapter_path: str | None = None,
                max_tokens: int = 512) -> Provider:
    """A Provider backed by transformers (+ optional PEFT adapter) — the FREE
    eval path: runs on a Colab/Kaggle T4 or any box that can load the base
    model, no Apple Silicon required. Raises a clear ImportError if the stack
    isn't installed — never degrades to empty responses."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as e:                        # pragma: no cover - env-dependent
        raise ImportError(
            "hf_provider needs torch+transformers (see training/cuda/"
            "requirements.txt) — free on Colab/Kaggle; see training/colab/."
        ) from e

    tokenizer = AutoTokenizer.from_pretrained(model_path)   # pragma: no cover
    model = AutoModelForCausalLM.from_pretrained(           # pragma: no cover
        model_path, device_map="auto",
        dtype=torch.float16 if torch.cuda.is_available() else torch.float32)
    if adapter_path:                                        # pragma: no cover
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter_path)
    tools = ronin_tools_for_template()

    def _provider(case: dict) -> ProviderResponse:  # pragma: no cover - needs a model
        ids = tokenizer.apply_chat_template(
            case_messages(case), tools=tools, add_generation_prompt=True,
            return_tensors="pt").to(model.device)
        out = model.generate(ids, max_new_tokens=max_tokens, do_sample=False,
                             pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
        text = tokenizer.decode(out[0][ids.shape[1]:], skip_special_tokens=True)
        return ProviderResponse(text=text, tool_calls=extract_tool_names(text))

    return _provider


def case_messages(case: dict) -> list[dict]:
    """The chat-template message list for a case. PURE.

    Single-turn: system + prompt. Multi-turn: a case may carry ``messages`` — a
    scripted PRIOR conversation (user/assistant/tool turns, mid-session) — and the
    model is asked for the next reply after it; ``prompt`` (when non-empty) is
    appended as the final user turn. This is how multi-turn stability is actually
    exercised instead of asserted about."""
    system = case.get("system", "You are Ronin.")
    msgs: list[dict] = [{"role": "system", "content": system}]
    msgs.extend(case.get("messages") or [])
    prompt = case.get("prompt", "")
    if prompt:
        msgs.append({"role": "user", "content": prompt})
    return msgs


def _sha256_file(path: str | Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git_sha() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


# The frozen eval set's pin lives IN GIT (training/data/ is gitignored, so the
# pin is the durable truth). An eval run against a set whose bytes don't match
# the pin is not comparable to any recorded number — refuse, loudly.
_EVAL_PIN = Path(__file__).resolve().parents[2] / "schemas" / "protocol_eval.sha256"


def assert_frozen_eval_set(evals_path: str | Path, *, repin: bool = False) -> list[str]:
    """[] if ``evals_path`` matches the committed pin. ``repin=True`` rewrites the
    pin (deliberate corpus evolution — never mid-comparison)."""
    p = Path(evals_path)
    if not p.is_file():
        return [f"eval set {p} missing — regenerate with the dataset builder"]
    digest = _sha256_file(p)
    if repin:
        _EVAL_PIN.write_text(digest + "\n", encoding="utf-8")
        return []
    if not _EVAL_PIN.is_file():
        return [f"no frozen pin at {_EVAL_PIN} — run once with --repin to freeze the set"]
    want = _EVAL_PIN.read_text().strip()
    if digest != want:
        return [f"eval set sha256 {digest} != pinned {want} — scores would not be "
                "comparable; regenerate the canonical set or deliberately --repin"]
    return []


def write_report(report: EvalReport, path: str | Path, *, title: str = "Protocol Eval",
                 provenance: dict | None = None) -> None:
    lines = [
        f"# {title}", "",
        f"- cases: **{report.total}**",
        f"- passed: **{report.passed}**  ·  pass rate: **{report.pass_rate:.1%}**", "",
    ]
    if provenance:
        lines += ["## Provenance", ""]
        lines += [f"- {k}: `{v}`" for k, v in provenance.items()]
        lines += [""]
    lines += ["## By category", "", "| category | passed | total |", "|---|---|---|"]
    for cat, (p, t) in report.by_category().items():
        lines.append(f"| {cat} | {p} | {t} |")
    lines += ["", "## Per-task", "", "| task | category | result |", "|---|---|---|"]
    for r in report.results:
        lines.append(f"| {r.eval_id} | {r.category} | {'PASS' if r.passed else 'FAIL'} |")
    fails = [r for r in report.results if not r.passed]
    if fails:
        lines += ["", "## Failures", ""]
        for r in fails:
            lines.append(f"- **{r.eval_id}** ({r.category}): {'; '.join(r.failures)}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Evaluate a real model (base or adapter) against the protocol eval set.

    Requires mlx-lm + a model; there is no stub provider, so this never prints a
    fabricated score. Example:
        python -m ronin_training.eval_runner \
            --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit \
            --evals training/data/evals/ronin_protocol_eval.jsonl \
            --out training/reports/base_eval.md --title "Base 1.5B"
    """
    import argparse
    ap = argparse.ArgumentParser(description="Run Ronin protocol evals against a model.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", default=None, help="path to a trained LoRA adapter")
    ap.add_argument("--evals", default="training/data/evals/ronin_protocol_eval.jsonl")
    ap.add_argument("--out", default=None,
                    help="report path (default: timestamped file in training/reports/)")
    ap.add_argument("--title", default="Protocol Eval")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--provider", choices=("mlx", "hf"), default="mlx",
                    help="mlx = Apple Silicon on-device; hf = transformers "
                    "(+PEFT) — the free Colab/Kaggle path")
    ap.add_argument("--baseline", action="store_true",
                    help="ALSO score the bare base model (no adapter) — every "
                    "adapter number ships next to its baseline delta")
    ap.add_argument("--repin", action="store_true",
                    help="deliberately re-pin the frozen eval-set hash (corpus "
                    "evolution only; forbidden mid-comparison)")
    ap.add_argument("--scores-json", default=None,
                    help="ALSO write machine-readable scores (per run: passed/"
                    "total/by_category/provenance) — what publish_model.sh "
                    "feeds the code-enforced ship gate")
    a = ap.parse_args(argv)

    frozen_errs = assert_frozen_eval_set(a.evals, repin=a.repin)
    if frozen_errs:
        raise SystemExit("FROZEN EVAL VIOLATION:\n- " + "\n- ".join(frozen_errs))

    cases = load_cases(a.evals)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    runs = [("adapter" if a.adapter else "model", a.adapter)]
    if a.baseline and a.adapter:
        runs.append(("baseline", None))

    scores: dict[str, EvalReport] = {}
    prov: dict[str, dict] = {}
    make = hf_provider if a.provider == "hf" else mlx_provider
    for label, adapter in runs:
        provider = make(a.model, adapter_path=adapter, max_tokens=a.max_tokens)
        report = run_evals(cases, provider)
        scores[label] = report
        prov[label] = {"model": a.model, "adapter": adapter or "(none)",
                       "commit": _git_sha(), "eval_set_sha256": _sha256_file(a.evals),
                       "timestamp": stamp}
        out = a.out or str(Path("training/reports") / f"eval{len(cases)}_{label}_{stamp}.md")
        write_report(report, out, title=f"{a.title} — {label}",
                     provenance=prov[label])
        print(f"{a.title} [{label}]: {report.passed}/{report.total} passed "
              f"({report.pass_rate:.1%}) → {out}")
        for cat, (p, t) in report.by_category().items():
            print(f"  {cat}: {p}/{t}")
    if len(scores) == 2:
        d = scores["adapter"].passed - scores["baseline"].passed
        print(f"baseline delta: {d:+d} (adapter {scores['adapter'].passed} vs "
              f"base {scores['baseline'].passed} of {len(cases)})")
    if a.scores_json:
        payload = {label: {"passed": r.passed, "total": r.total,
                           "by_category": {k: list(v) for k, v in r.by_category().items()},
                           "provenance": prov[label]}
                   for label, r in scores.items()}
        Path(a.scores_json).write_text(json.dumps(payload, indent=2) + "\n",
                                       encoding="utf-8")
        print(f"scores json: {a.scores_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
