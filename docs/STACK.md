# Stack

The decisions, one line of justification each, and — where a decision is not yet the
truth on disk — what is actually there. A stack document whose status column is
aspirational is worse than no stack document: it makes every reader who trusts it wrong
about the codebase they are standing in.

**Status key.** ✅ decided and true today · ⚠️ decided, partially true · ⛔ decided, **not
implemented** — the line says what exists instead.

---

## Core language — Python 3.11+ ✅

Fast iteration, the entire ML ecosystem, and the fact that almost every reference
implementation an agent harness needs to read is Python; a language that makes you leave
the ecosystem to fine-tune a model is the wrong language for a project whose wedge is
fine-tuning a model.

**What must be TypeScript, and where the wall is.** Six `package.json` files exist; only
three hold any TypeScript, and each is justified by a **JS runtime being mandatory** —
never by convenience:

| surface | ts/tsx | why it cannot be Python |
|---|---|---|
| `apps/web` | 45 | Next.js app; the browser runs JS and that is not negotiable |
| `packages/design-system` | 16 | React components, consumed by `apps/web` only |
| `editors/vscode` | 1 | the VS Code extension host is Node — no choice available |

The other three (`apps/demo`, `apps/docs`, `packages/agent-patterns`) carry a
`package.json` with **zero** `.ts` files and no dependencies: they are pnpm-workspace
shells around Python packages. That is clutter, not architecture, and it makes the TS
surface look twice its real size to anyone counting manifests — worth deleting, and listed
here so the count in this table cannot be quietly contradicted by `find`.

**The wall.** All three real surfaces sit behind a separate `pnpm` workspace with no
Python import path into or out of them, and none of them contains agent logic.
`editors/vscode` is the load-bearing case: an editor extension is exactly where somebody
would be tempted to reimplement the loop in TypeScript, and it must not — it holds **one**
`.ts` file and talks to the Python agent over **ACP**, which stays Python
(`packages/cli/src/ronin_cli/acp.py`) because it is JSON-RPC over stdio, not a JS runtime
dependency. The rule: TypeScript may draw things and host extensions; it may never decide
anything the agent depends on.

## Async runtime — asyncio ✅

Standard library, so it costs nothing and every provider SDK and MCP transport already
speaks it; trio would be a better concurrency model and an ecosystem tax nobody here can
afford to pay.

## TUI — Textual ✅

Mouse, panes and a live approval modal are worth the dependency, and it is already the
choice in `ronin.ui.app`; **note that this is not Textual *versus* Rich** — Textual brings
Rich transitively, so it is one dependency tree, and it is an **optional extra** (`pip
install ronin[tui]`) because the headless path is the one CI and scripts use and it must
work on a bare install.

## Packaging — uv + `pyproject.toml` ✅, shipped via pipx ⚠️

uv for the workspace because 23 packages resolve in seconds and the lockfile is
reproducible, and pipx for the user because an agent that edits repositories must not live
in the virtualenv of the project it is editing.

**pipx is the decision, not yet the path.** `install.sh` today clones the repo, runs `uv
sync --all-packages`, and drops `ronin`/`ro` shims in `~/.local/bin` pointing at the
workspace venv — which works, and means every user has a git checkout they did not ask
for. The file itself says `pipx install ronin-cli` is the intended end state. `ronin2`
now has an entry point, so `pipx install ronin` is a one-line install the moment the
distribution is published.

## Process model — one persistent event loop, subprocess for the shell ✅

One loop keeps the whole turn cancellable from a single place, and shelling out means a
hung `pytest` is a process you can kill rather than a thread you cannot; `ronin.tools.shell.PersistentShell`
keeps one long-lived shell so `cd` and exported variables survive between commands, which
a fresh subprocess per call cannot.

## Data ⚠️

Four stores, split by what each one is *for* — and the split is not the obvious one. The
principle: **append-only for what happened, queryable for what is derived, plain files for
what a human owns.** A single store would force one of those three to be wrong.

- **Transcripts — JSONL, append-only.** ✅ One `.ronin/sessions/<id>.jsonl` per session,
  one JSON object per line, fsync at turn boundaries. Append-only is the point: the
  record of what happened must not be revisable by the thing that is still happening.
- **Session index — sqlite, WAL.** ⛔ **Not implemented.** Today it is a
  `.ronin/sessions/<id>.meta.json` sidecar, rewritten atomically per turn, with
  `list_sessions` falling back to the JSONL's first line and marking the row `stale`. That
  is O(sessions) and correct, so this is an upgrade rather than a bug fix — but a sidecar
  per session does not answer "which sessions touched this file" without reading all of
  them, and sqlite does.
- **Cost ledger — sqlite.** ⚠️ Real and used (`ronin.providers.accounting.Ledger`), but
  it opens the connection with **no PRAGMA at all** — so no WAL, no `busy_timeout`. The
  consequence is concrete: a background agent and an interactive session both billing to
  the same ledger will block each other on the default rollback journal instead of the
  reader-writer concurrency WAL gives.
- **Memory — plain files.** ✅ Markdown a human can read, diff and commit. A database
  would make the agent's memory something you need the agent to inspect.

## Test — pytest + pytest-asyncio (`asyncio_mode = "auto"`) ✅, coverage gate 85% ⚠️

pytest because it is what everything else in Python assumes, and `asyncio_mode = "auto"`
because an agent is async top to bottom and marking three thousand tests by hand is a tax
with no benefit.

**The gate is 85% on `src/ronin`, and it was measured before being written down** — this
project does not state thresholds it has no baseline for. `pytest-cov` was added for that
one purpose, and the run says:

| scope | covered | statements | |
|---|---|---|---|
| all of `src/ronin` | 14,752 | 17,160 | **86.0%** |
| excluding the 11 demo/entrypoint modules | 14,752 | 15,654 | **94.2%** |

The gap between those two rows is the useful finding. **Eleven `demo.py` modules totalling
1,506 statements sit at exactly 0%** — every subsystem ships a demo command by standing
convention, and no test runs any of them. So a naive 85% gate over everything passes today
with **1.0 point of headroom**, which makes it a tripwire that fires on the next demo
module somebody adds rather than on a real regression; and the same 85% over the code that
matters has **9.2 points of slack**, which gates nothing at all.

The honest gate is therefore two decisions, not one: **exclude the demo modules from
measurement** — a demo whose only caller is a human is not code tests should be asserting
about — and **set the threshold near the real number**, 90% rather than 85%, so it has
teeth without being a ratchet. ⚠️ Neither is enforced in CI yet; the numbers above are a
measurement taken today, not a gate.

One genuine low spot worth naming rather than averaging away:
`providers/local_adapter.py` is at **58%**, because its generation paths need `mlx_lm` or
`transformers` and CI has neither. That is a real limit of an offline test suite, not an
oversight — but it means the `$0` lane's inference code is the least-exercised code in the
tree, which is worth knowing before trusting a number it produces.

## Lint and types — ruff + mypy `--strict` ✅

One tool for lint and format instead of three, and `--strict` over `src/ronin` because a
harness whose tool arguments are `dict[str, Any]` cannot tell you a model sent the wrong
shape.

The scope is deliberate and worth stating precisely, because it is the kind of thing that
reads as thoroughness and is actually a boundary: `files` lists `src/ronin` plus **13
named `tests/` directories**, and nothing else. `packages/*` — the ~300-module v1 tree —
is **not** strict-clean and is not checked; `training/` is not either. Widening the setting
to cover them would mean a wave of suppressions or a weaker setting everywhere, and both
are worse than an honest boundary. `scripts/sync_typecheck_paths.py --check` fails the
build when a test directory is missing from that list, because a directory absent from
`files` has no type checking at all and nothing else reports it.

---

## The hard rule

**No framework owns the agent loop.** Not LangChain agents, not AutoGen, not CrewAI, not
LlamaIndex query engines — nothing that decides when to call a tool, when to stop, or what
to do about a failure. `ronin.core.loop` is the loop, in this repository, readable in one
sitting.

This is not taste. The loop is where every property this project sells actually lives: the
approval gate sits between deciding to call a tool and calling it; the stall detector
needs the fingerprint of the last three tool calls; the taxonomy needs to know whether a
failure was a malformed argument or a refused permission; the trajectory harvest needs the
exact turn boundaries the model saw. A framework that owns control flow owns all four, and
you get them back only as whatever hooks it chose to expose. The eval suite would then be
measuring somebody else's control flow with our name on the result.

**Libraries for I/O, never for control flow.** `httpx` to move bytes, `sqlite3` to store
them, `textual` to draw them, `tree-sitter` to parse them — each reached through a lazy
import inside the one function that needs it, each degrading with a named error rather
than an `ImportError` traceback. The test: if a dependency would appear in a stack trace
*between* "the model asked for a tool" and "the tool ran", it is the wrong dependency.

The evidence that this is affordable: `pyproject.toml` declares **zero hard
dependencies**. Every capability is an extra, and `pip install ronin2` gives a working
agent rather than a broken one waiting for its extras.
