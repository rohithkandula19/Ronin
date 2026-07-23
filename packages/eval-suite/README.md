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
ronin-eval run golden.jsonl --target claude-sonnet-4-6 --judge claude-opus-4-7 --out report.html
ronin-eval drift baseline.json candidate.json --threshold 0.5
```

`ronin-eval drift` exits non-zero on regression — wire it into CI to catch quality drops before merge.

## Dataset format (JSONL)

```jsonl
{"id": "case_1", "input": "What is the ReAct pattern?", "expected": "Reasoning + Acting loop with tool use."}
{"id": "case_2", "input": "Summarize this contract: ...", "metadata": {"category": "summarization"}}
```

`expected` is optional. Lines starting with `#` and blank lines are skipped.

## SWE-bench (execution-based eval)

The judge suite above scores with a model. **SWE-bench** scores by *running
tests*: each task is a real repo bug with two test sets — `FAIL_TO_PASS` (must
pass after the fix) and `PASS_TO_PASS` (must not regress). A task is **resolved**
iff every required test passes once the agent's patch is applied.

The harness is execution-agnostic — plug in your agent (`patch_runner`) and an
executor (`evaluator`):

```python
from ronin_eval_suite import (
    SWEBenchDataset, SWEBenchHarness, make_local_git_evaluator,
    oracle_runner, render_swebench_markdown,
)

dataset = SWEBenchDataset.from_jsonl("tasks.jsonl")  # accepts the official JSONL

harness = SWEBenchHarness(
    patch_runner=lambda task: my_agent.solve(task.problem_statement),  # -> unified diff
    evaluator=make_local_git_evaluator("/path/to/checkout"),           # applies + runs tests
    model="ronin · gemini",
)
report = harness.run(dataset)
print(report.summary["resolved_rate"])
print(render_swebench_markdown(report))            # paste-ready results table
```

Sanity-check your environment with `oracle_runner` (runs the *gold* patches —
they should all resolve). `compare_swebench(a, b)` diffs two runs for
regressions.

### CLI

```bash
# score a standard predictions file ({instance_id, model_patch} JSONL):
ronin-eval swebench tasks.jsonl --predictions preds.jsonl --repo-root ./checkout --markdown out.md
# gate CI on regressions between two runs (exits non-zero if any task broke):
ronin-eval swebench-compare baseline.json candidate.json
```

`FAIL_TO_PASS` / `PASS_TO_PASS` load from either JSON arrays or the official
dataset's JSON-encoded strings. See `examples/swebench_sample.jsonl` for the
format. The harness needs no API key, Docker, or network — those are concerns of
whichever `patch_runner` and `evaluator` you supply.

> Honesty note: this ships the **harness**, not a published score. Run it
> against SWE-bench Lite/Verified with your provider to produce numbers.

## Failure semantics

- A target crash on one case records an error and continues the run.
- A judge parse failure records an error on that case and continues.
- The summary mean is computed over non-errored cases only.

## Tests

```bash
uv run --frozen pytest packages/eval-suite -q
```

No API key needed — tests mock the Anthropic client.
