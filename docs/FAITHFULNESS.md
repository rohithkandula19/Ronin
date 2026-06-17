# Faithfulness: a grounding harness for agent answers and edits

The injection scanner guards what goes into the agent. The faithfulness harness
guards what comes out. It answers one question about a finished answer or a
proposed file edit: is every claim supported by the sources the agent actually
used (files it read via tools, tool outputs, retrieved context), or did the model
state something it never saw?

This is a faithfulness / grounding harness in the standard sense (claim
decomposition plus per-claim grounding against retrieved evidence), with a code
specialization: it flags references to functions, attributes, files, and APIs
that do not appear in anything the agent read. It is lexical and deterministic by
default, so it runs in tests with no provider, no keys, and no network. An
optional semantic judge can be plugged in for paraphrase that lexical overlap
misses; it is consulted only for claims the lexical pass leaves ungrounded, so
the default path stays fully offline.

It does not claim novelty over the grounding-check literature. What it does is
fit Ronin's architecture: it reads the same execution trace the ReAct loop
already produces, lives beside the existing hardening checks, and is exercised
end to end through the in-memory mock provider.

## What it does

Given an answer and a list of sources, the harness:

1. Decomposes the answer into atomic claims (sentence and clause segmentation;
   each fenced code block is graded as one unit).
2. Grounds each claim against the union of sources, via content-word overlap
   plus exact code-symbol presence.
3. Detects hallucinated symbols: identifiers, dotted attribute paths, file
   paths, and calls referenced in the answer that do not appear in any source.
4. Produces a calibrated 0..1 faithfulness score with a per-claim
   grounded / ungrounded breakdown and the list of hallucinated symbols.
5. Abstains when the evidence is too thin to judge honestly: no sources, no
   scorable claims, or a score that lands in the configured uncertain band.

## Module layout

Package `ronin-hardening`, module `ronin_hardening/faithfulness.py`, a sibling of
`injection.py`. Public names are re-exported from `ronin_hardening/__init__.py`:

- `Source` -- one piece of evidence (`origin`, `text`).
- `sources_from_trace(trace, *, evidence_tools=None, include_all_tools=False)`
  -- pull observed sources out of an `AgentResult.trace`.
- `split_claims(answer)` -- decompose an answer into atomic claims.
- `extract_symbols(text)` -- extract code-shaped symbols from text.
- `ClaimVerdict` -- per-claim result (`claim`, `grounded`, `support`, `method`,
  `ungrounded_symbols`).
- `FaithfulnessReport` -- full report (`score`, `abstain`, `reason`, `claims`,
  `hallucinated_symbols`, `n_sources`; plus `grounded_fraction` and
  `ungrounded_claims` properties).
- `FaithfulnessHarness` -- the entry point: `check(answer, sources)` and
  `check_edit(new_code, sources)`.

## Public API

```python
from ronin_hardening import FaithfulnessHarness, Source, sources_from_trace

harness = FaithfulnessHarness(
    support_threshold=0.6,   # min content-word overlap for a lexical claim
    abstain_low=0.45,        # scores strictly inside (low, high) -> abstain
    abstain_high=0.65,
    min_sources=1,           # below this many sources -> abstain
    semantic_judge=None,     # optional (claim, sources_text) -> float in [0,1]
    semantic_threshold=0.6,
)

report = harness.check(answer_text, sources)   # sources: list[Source]
report.score             # 0..1 calibrated faithfulness
report.abstain           # True when the harness declines to judge
report.reason            # "ok" or why it abstained
report.hallucinated_symbols
for v in report.claims:
    v.claim, v.grounded, v.support, v.method, v.ungrounded_symbols
```

`method` is one of `lexical`, `symbol`, `semantic`, or `trivial`. A `trivial`
claim (a header or connective with no checkable assertion) does not count for or
against the score.

### Scoring

The score blends the grounded-claim fraction with a hallucinated-symbol penalty:

```
score = max(0, grounded_fraction - 0.5 * (claims_with_hallucinated_symbol / scored_claims))
```

A single fabricated API therefore drags the score down even when surrounding
prose overlaps the sources. An answer with no scorable claims scores 0.0 and the
harness abstains rather than awarding a free 1.0 to an empty answer.

### Abstain

The harness abstains when:

- there are fewer than `min_sources` sources (nothing to ground against), or
- the answer has no scorable claims, or
- the score lands strictly inside `(abstain_low, abstain_high)`.

A clear hallucination is not "uncertain": a fabricated symbol pushes the score
firmly below the band, so it is reported as ungrounded, not abstained.

## How it hooks the agent loop

The ReAct loop (`ronin_agent_patterns/react.py`) records every tool result as a
`Step(kind="tool_result", content={"name", "result", "is_error"})` and the answer
as `Step(kind="final")`, all collected in `AgentResult.trace`. The harness reads
that trace directly; no change to the loop is required.

```python
from ronin_hardening import FaithfulnessHarness, sources_from_trace

result = agent.run(user_message)               # AgentResult
sources = sources_from_trace(result.trace)     # files read, tool outputs
report = FaithfulnessHarness().check(result.output, sources)

if report.abstain or report.score < 0.5:
    # surface the ungrounded claims, ask the agent to cite, or hold the answer
    for v in report.ungrounded_claims:
        ...
```

`sources_from_trace` treats `read_file`, `search_files`, `list_files`, `glob`,
`grep`, and web-fetch results as evidence by default and skips errored results
and the agent's own writes. Pass `evidence_tools=...` to override the set, or
`include_all_tools=True` to count every non-error tool result.

For a proposed file edit, score the new code against what the agent read. The
edit primarily trips the hallucinated-symbol check: a write that calls a helper
absent from every file the agent opened is the classic ungrounded edit.

```python
report = FaithfulnessHarness().check_edit(new_string, sources)
if report.hallucinated_symbols:
    # the edit references symbols not present in any file the agent read
    ...
```

This composes with the rest of the hardening package: run the injection scanner
on the input, the faithfulness harness on the output, and gate sensitive tools
with the approval gate.

## Offline test strategy

Every test runs with no provider, no keys, and no network.

- Unit tests construct `Source` objects directly from a small fixed code snippet
  and assert that grounded answers score high, fabricated answers score low,
  hallucinated symbols are named, and the two diverge for the same harness (a
  stubbed "always grounded" implementation fails this, so the grounding signal
  has to be real).
- Calibration and abstain tests assert the uncertain band and the no-sources and
  no-scorable-claims paths.
- The optional semantic judge is tested with a plain Python callable, proving it
  is consulted only for claims the lexical pass leaves ungrounded.
- Two integration tests drive the real `ReActAgent` through the in-memory
  `FakeProvider`: the agent reads a file, answers, and the harness scores the
  answer off `sources_from_trace(result.trace)` -- one grounded run and one where
  the canned answer fabricates a function, which the harness flags.

Run the harness tests:

```
uv run pytest packages/hardening/tests/test_faithfulness.py
```
