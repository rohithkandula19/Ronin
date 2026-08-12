# RONIN.md — project rules

- python 3.11+, strict typing, mypy clean, ruff clean
- every module ships with tests in the same change; offline only, no network in tests, mock
  all providers
- no phase is done without unit tests, one integration test, and a demo command i can run
- prefer stdlib; justify every new dependency in one line
- all state changes go through one place, no hidden globals
- tools return values not exceptions across boundaries: ToolResult(ok, content, error)
- every tool result the model sees is truncated deterministically with a marker showing what
  was cut
- read a file before editing it
- if a design decision has two reasonable options, stop and ask me, do not build both
