# ronin — agent quality benchmark

How well does the agent actually complete real tasks? `ronin eval` answers that
with an **objective, deterministic** battery — no LLM-as-judge — so the score is
reproducible and works on any provider.

```bash
ronin eval                 # score the current provider/model
ronin eval --model <name>  # score a specific model
```

## Method

Six tasks, each run in a throwaway sandbox; each is scored on its **outcome**
(did the file get created? is the answer correct? was the tool used?), not on a
judge's opinion:

| Task | What it tests |
|---|---|
| `arithmetic` | reasoning |
| `write_file` | file write via tool use |
| `codegen` | code generation |
| `read_grounded` | reading a file + a grounded answer |
| `multi_file` | a multi-file task |
| `instruction` | exact instruction-following |

## Results

### `cerebras · gpt-oss-120b` — verified live

| Task | Result | Steps | Tokens |
|---|---|---|---|
| arithmetic | ✓ | 1 | 764 |
| write_file | ✓ | 2 | 1,499 |
| codegen | ✓ | 2 | 1,682 |
| read_grounded | ✓ | 2 | 1,487 |
| multi_file | ✓ | 3 | 2,756 |
| instruction | ✓ | 1 | 713 |

**6/6 passed (100%)** · ~8.9k tokens. Reproduce: `ronin eval --model gpt-oss-120b`.

## A note on cross-model benchmarking (a real finding)

Benchmarking several models back-to-back on a **single free-tier key** is
unreliable: the first model consumes the per-minute/daily quota, so subsequent
models get throttled (HTTP 429) and record **0 completed calls** — which looks
like "0% quality" but is actually "no fair attempt." ronin's eval surfaces this
honestly (0 tokens ⇒ no successful calls, not a real score).

To compare models fairly you need either a paid tier, separate keys per provider,
or enough spacing for the rate-limit window to reset between runs. This is the
kind of confound worth naming rather than papering over with a misleading table —
the same reason the harness scores outcomes, not vibes.

> Run it yourself: `ronin eval --model qwen-3-235b-a22b-instruct-2507`,
> `ronin eval --model gpt-oss-120b`, `/login gemini` then `ronin eval`, etc.
