# ronin-eval-suite

LLM-as-a-judge eval suite for Claude agents. Golden datasets, configurable rubrics, drift detection, self-contained HTML reports, CLI runner.

## Quickstart

```python
from ronin_eval_suite import EvalSuite, Rubric, GoldenDataset, render_html_report

dataset = GoldenDataset.from_jsonl("./golden.jsonl")

suite = EvalSuite(
    rubric=Rubric(criteria=["task_success", "faithfulness", "safety"]),
    target_model="claude-sonnet-4-6",
    judge_model="claude-opus-4-7",
    # Plug in any callable that takes an EvalCase and returns a string output:
    target_runner=lambda case: my_agent.run(case.input).output,
)

report = suite.run(dataset)
render_html_report(report, "./eval-report.html")
print(report.summary)
```

## CLI

```bash
csk-eval run golden.jsonl --target claude-sonnet-4-6 --judge claude-opus-4-7 --out report.html
csk-eval drift baseline.json candidate.json --threshold 0.5
```

`csk-eval drift` exits non-zero on regression — wire it into CI to catch quality drops before merge.

## Dataset format (JSONL)

```jsonl
{"id": "case_1", "input": "What is the ReAct pattern?", "expected": "Reasoning + Acting loop with tool use."}
{"id": "case_2", "input": "Summarize this contract: ...", "metadata": {"category": "summarization"}}
```

`expected` is optional. Lines starting with `#` and blank lines are skipped.

## Failure semantics

- A target crash on one case records an error and continues the run.
- A judge parse failure records an error on that case and continues.
- The summary mean is computed over non-errored cases only.

## Tests

```bash
uv run --frozen pytest packages/eval-suite -q
```

No API key needed — tests mock the Anthropic client.
