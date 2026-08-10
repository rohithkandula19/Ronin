# Engineering rules

The non-negotiables. `CONTRIBUTING.md` covers *how* to open a PR; this covers what the
code has to be like before it gets merged. Where the two disagree, this file wins.

These are short on purpose. A rule you have to look up is a rule nobody follows.

## Cost

**No paid resource, no paid API, for anything required.** The `$0` lane is not a
courtesy tier — it is the lane the project is built to prove. Local weights, a local
OpenAI-compatible server, a free Colab session. If a feature only works with a hosted key,
it is an optional extra and its absence must degrade with a named error, never a crash.

**Never invent a number.** No score, benchmark result or coverage figure appears in any
file unless a real run produced it. `docs/POSITIONING.md` §5 names its central numbers as
unmeasured, three times, because they are. A plausible number is worse than no number: it
cannot be reproduced and it cannot be corrected, because nobody knows it is wrong.

## Language and types

Python 3.11+. `mypy --strict` clean and `ruff` clean over the configured tree — both are
CI gates, both run in pre-commit, and the scope is exactly `[tool.mypy] files` and
`[tool.ruff] include` in `pyproject.toml`. TypeScript only where a JS runtime is
mandatory (see `docs/STACK.md`), and TypeScript never decides anything the agent depends
on.

## Tests

**Every module ships with tests in the same PR.** Not the next one.

**Offline only.** No network in tests, every provider mocked. This is enforced, not
trusted: `scripts/check_test_imports.py` parses each test file's AST and fails the commit
on an import of `httpx`, `requests`, `socket` and the rest. It walks the AST rather than
grepping because a docstring mentioning `import httpx` is not an import — a grep-based
version failed on a correct file.

**No phase ships without** unit tests, one integration test, and a demo command a human
can run. `make eval` is the model for the last one: it works with no key and opens no
model.

**Coverage gate: 85% over all of `src/ronin`**, threshold in `[tool.coverage.report]` so
there is one number rather than one per caller. It was measured before being written
down.

## Dependencies

**Prefer stdlib.** Justify every new dependency in one line, in the file that adds it.
The root project declares **zero hard dependencies**; every capability is an extra reached
through a lazy import inside the one function that needs it.

**No framework owns the agent loop.** Not LangChain, not AutoGen, not CrewAI. The test: if
a dependency would appear in a stack trace *between* "the model asked for a tool" and
"the tool ran", it is the wrong dependency. Libraries for I/O, never for control flow.

## Design

**All state changes go through one place.** No hidden globals. If two code paths can
mutate the same thing, one of them is a bug waiting to be filed.

**Errors are values, not exceptions, across tool boundaries.** Tools return
`ToolResult(ok, content, error)`. An exception crossing that boundary is a crash the model
cannot recover from; a value is something it can read and retry.

**Every tool result the model sees is truncated deterministically, with a marker showing
what was cut.** Non-deterministic truncation makes a transcript unreplayable, and a silent
truncation teaches the model that the file ended where it did not.

## When to stop and ask

**Two reasonable options → ask. Do not build both.** Building both doubles the surface,
halves the conviction, and leaves the decision to whoever reads the code next with less
context than you have now.

This applies to real forks, not to every small choice. If one option is clearly better,
take it and say why in the commit message.

## Documentation

State what is true on disk today, and mark what is not. `docs/STACK.md` uses ✅ / ⚠️ / ⛔
for exactly this reason: a document whose status column is aspirational makes every reader
who trusts it wrong about the code they are standing in. If a decision is made but not
implemented, the doc says so and says what exists instead.
