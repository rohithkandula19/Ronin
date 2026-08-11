# The tool layer

> This is the agent's entire body, so precision beats breadth.

Thirteen tools. Each is a class with a name, a description **written as a prompt**, a
JSON schema, a danger level, and one `async run(args, ctx)`.

```
uv run python -m ronin.tools.demo     # all of it, in a temp directory, real shell
```

---

## The three rules

Everything else in this layer is detail. These three are load-bearing.

**1. `write` refuses a file that has not been read this session.**
One rule, and it prevents most destructive edits. A model that has read the file knows
what it is replacing; one that has not is guessing, and a full overwrite of a guess
destroys work nobody asked it to touch. This only works because "has been read" is
*session* state — `ToolContext.read_files` — rather than something checked per call.

**2. `edit` matches exactly, and ambiguity is an error.**
No fuzzy matching, no whitespace normalization, no "did you mean". Two matches with
`replace_all=False` **fails**, and the error says how many matches there were and what
to do about it (extend `old_string` with surrounding context). A search-and-replace that
matches twice and picks one has corrupted a file in a way the model will confidently
report as success.

**3. `bash` is one persistent shell.**
`cd`, `export` and an activated venv survive across calls, because it is the same
process throughout. With a subprocess per call, `cd src` then `pwd` prints the original
directory, and a model that cannot rely on its own `cd` re-derives its environment on
every command and eventually gets it wrong.

---

## Tools

| Tool | Danger | Gated | Notes |
|---|---|---|---|
| `read` | read-only | | `cat -n` format, 2000-line cap, images as blocks when the model has vision |
| `write` | mutating | ✅ | refuses an unread existing file |
| `edit` | mutating | ✅ | exact match; ambiguity is an error |
| `multi_edit` | mutating | ✅ | sequential on an in-memory buffer, all-or-nothing |
| `glob` | read-only | | ripgrep `--files`, newest first |
| `grep` | read-only | | ripgrep; `content`/`files`/`count`, `-A/-B/-C`, `-i`, `-n`, multiline |
| `ls` | read-only | | one level, with ignore patterns |
| `bash` | destructive | ✅ | persistent shell, timeout, output cap, background mode |
| `bash_output` | read-only | | tails a background job, new output only |
| `todo_write` | read-only | | the model's plan, rendered in the UI |
| `task` | read-only | | nested loop, fresh context, returns only a summary |
| `web_fetch` | read-only | | fetch → markdown → fast model → the answer, cached 15 min |
| `web_search` | read-only | | titles, URLs, snippets — brave/tavily/searxng, off unless configured |

---

## Writing tool descriptions

Half of agent quality is here, so the shape is enforced by `Tool.spec()` rather than
left to each tool's discretion. A tool that does not say **when not to use it**, or
carries no worked example, fails to build:

```python
raise ValueError(
    f"tool {self.name!r} must say when to use it — a description that "
    "only says what it does gets called in situations it should not be"
)
```

Rendered in a fixed order (summary → WHEN TO USE → WHEN NOT TO USE → EXAMPLE), because
a model reading thirteen descriptions benefits more from all of them having the same
shape than from any one being cleverly written.

The redirects live in `bash`'s description as well as in its behaviour — *"use the grep
tool instead of `grep`"* — because a refusal at call time costs a turn, and a
description read before the call costs nothing.

**Every error message is a prompt too.** "invalid path" tells the model nothing. These
tell it what to do next:

```
src/app.py already exists and has not been read in this session. Writing would
replace its entire contents with something chosen without seeing what is there.
Call read first — then either write, or use edit to change only the part you mean to.

old_string appears 3 times in src/util.py, so replacing it would be ambiguous.
Either extend old_string with the surrounding lines until it is unique, or pass
replace_all=true if you genuinely mean to change all 3 occurrences.
```

---

## Search wraps ripgrep; it does not reimplement it

ripgrep already handles gitignore, binary detection, encoding sniffing, parallel
traversal and multiline matching. A Python reimplementation would be slower and wrong in
ways nobody notices until it matters. If `rg` is missing the tools **say so with an
install hint** rather than falling back to something worse — a quiet fallback that skips
gitignored files differently is how a model concludes a symbol does not exist when it
does.

Three flags are not ripgrep's defaults, and all three were found by tests:

- **`--no-require-git`** — by default ripgrep honours `.gitignore` only *inside* a git
  repository. An agent in a checkout of a subdirectory would get `build/` and
  `node_modules/` in its results.
- **`--with-filename`** — given exactly one file, ripgrep omits the filename, so a
  single-file search returns `12:    return None` and the model has a line number with
  nothing to apply it to.
- **paths relative to the root, with `./` stripped** — an absolute prefix repeated on
  every match line is pure token cost, and the model feeds these paths straight back
  into `read`.

---

## The shell

One `bash --noprofile --norc`, driven by sentinel round-trips: write the command, then
`printf '%s %s\n' SENTINEL "$?"`, and read until that sentinel appears. The sentinel is
a fresh uuid per shell, so a command that *prints* the sentinel text cannot fake a
completion.

**Not `-i`, despite the work order specifying it.** Interactive bash with a pipe for
stdin echoes every command back before running it, prints `cannot set terminal process
group` and `no job control in this shell` on startup, and emits a prompt string — all of
which land in the model's result. The persistence comes from the process being
long-lived, not from interactive mode, and with `--norc` there are no aliases left for
`-i` to enable. It buys nothing and costs noise on every call. (Verified: with `-i` the
first assertion of the persistence test failed on echoed input.)

Three failure modes get explicit handling because each one otherwise hangs a turn:

- **Timeout** kills the whole process **group**. A command that spawned children leaves
  them holding the pipe, and killing only bash waits forever on a read that never
  finishes. There is a test that asserts the grandchild died.
- **Huge output** is capped keeping **head and tail**, 40/60. The tail is where the
  traceback is; a head-only cap throws away the reason the command failed.
- **A command that never returns** gets `run_in_background=true`, which returns a handle
  immediately, and `bash_output(handle)` tails it.

### Refusals

`grep`, `rg`, `find`, `cat`, `head`, `tail`, `ls` are refused with a pointer to the
tool to use instead. Checked **per `;`/`&&`/`||`/`|` segment** and past a leading
`sudo`/`env`/`time`, so `cd src && grep foo` is caught — a check that only looks at the
front of the string is one that gets bypassed by accident.

Interactive programs (`vim`, `less`, `top`, `man`) are refused because there is no
terminal and they would hang until the timeout.

`exit` and `logout` are refused, **found by a test**: `exit 3` looks like "return exit
code 3" and instead ends the persistent shell, silently discarding the cwd, exports and
venv that every later command depends on. The message points at `(exit 3)`.

A short list of destructive shapes (`rm -rf /`, `mkfs`, `dd of=/dev/sd*`, fork bomb,
`chmod 777 /`) is refused outright. Deliberately short: a long blocklist gives false
confidence, and the real boundary is the approval gate plus a sandbox. These are the
ones where an approval prompt arrives too late to help.

---

## Subagents

`task(prompt, subagent_type)` spawns a nested loop with fresh context, its own tool
subset and its own budget, and returns **only a final summary**. The parent never sees
the intermediate turns — that is the entire point: forty grep hits and eight file reads
cost the subagent's context, not the parent's. It uses the `fast` model
(`providers.router`), because triage and summarizing are mechanical work.

The runner is **injected**. The tool layer must not import the loop, so
`SubagentRunner` is a callable the caller supplies. Both built-in types (`explore`,
`summarize`) are read-only, which is what makes spawning one a cheap decision rather
than a risky one — asserted by a test.

`task`'s description lists the registered types **dynamically**: a model told about a
type that is not registered will call it and get an error, and a model not told about
one that is will never use it.

---

## The plan

`todo_write` looks like a UI nicety and is not. A model that writes down a plan and
crosses items off stays coherent over a long task in a way the same model does not when
it holds the plan in its head; the list survives compaction, and it is the thing the
user reads to know the agent is still on the thing they asked for. Required for any task
with three or more steps, and the description says so — a threshold that is not in the
prompt is a threshold the model will not apply.

Two invariants are enforced: **exactly one item in progress** (two means the model has
lost track; zero while work remains means nothing is driving), and the full list every
call rather than a delta.

`Todo` in the merged contract calls the text `subject`; models overwhelmingly write
`content`. Both keys are accepted rather than making the model learn ours. Ids are
positional and derived, because a model asked to invent stable ids across calls reuses
them inconsistently and the list is replaced wholesale anyway.

---

## `web_fetch` returns the answer, not the page

A modern HTML document is tens of thousands of tokens of navigation, cookie banners and
script tags. Handing that to the main model spends most of a context window to answer
one question. So: fetch → reduce to markdown → hand to the **fast** model with the
caller's prompt → return the extraction. Cached 15 minutes, because reading the same
changelog four times in one task is normal and paying for it four times is not.

The result names the URL and the content hash, because a summary can be wrong and a
caller who needs exact wording should ask for it in the prompt.

**Where it is allowed to go.** The client (`tools/fetcher.py`) asks `safety/net.py` twice:
once about the URL as written, and once about what the hostname *resolves to*. The second
question is the one that matters, because `https://docs.example.com/` is a perfectly
well-formed URL and publishing an `A` record that points at `169.254.169.254` is the
ordinary way to reach a cloud metadata endpoint from someone else's network. The vetted
addresses come back and the connection uses **them**, with the hostname kept in the `Host`
header and the TLS handshake so certificate verification still means something — a client
that vetted a name and then dialled the name asked DNS twice and trusted the second
answer. Redirects are followed here rather than inside the library so each hop goes
through the same two questions.

Both questions read the IPv4 address an IPv6 address can *carry*: 6to4, Teredo, NAT64,
`::a.b.c.d`, `::ffff:a.b.c.d` and **ISATAP** (`<prefix>:0:5efe:<ipv4>`). ISATAP is the one
worth naming, because its prefix is ordinary public address space — nothing about
`2001:470:1f0b:1:0:5efe:a9fe:a9fe` looks internal until you read the last 32 bits, where
169.254.169.254 is sitting. Unwrapping only ever *adds* a refusal: a 6to4 address wrapping
a public IPv4 is still refused for being 6to4.

The HTML→markdown reduction is deliberately small and is *not* a parser: it deletes
scripts, styles, comments and tags while keeping headings and list structure, since
those are what let the fast model find the section that answers the question. A real
parser would be a dependency for a marginally better result.

`localhost`/`127.0.0.1` is refused — if you mean a local dev server, `curl` it with
`bash` so the request is explicit.

---

## Tests

225 tests, every tool against a real temp directory.

- **`edit` fuzz**: 19 parametrized cases covering emoji, CJK, combining marks, RTL,
  CRLF, mixed line endings, tabs vs spaces, trailing whitespace, form feed, zero-width
  space, NBSP, regex metacharacters, backslashes, `${…}`, a 5000-character line, no
  trailing newline, and a file of only newlines. Each must either apply **exactly** or
  refuse — never silently mangle. Plus identical repeated blocks, where the bare edit
  must fail and the same edit scoped by its enclosing `def` must succeed and touch one
  of the two.
- **`bash`**: persistence (`cd` then `pwd`, `export` then read back, a function defined
  once), timeout including a grandchild-killed assertion, and a 20,000-line output
  asserting head *and* tail survive.
- **Descriptions**: one test walks all thirteen specs and asserts WHEN TO USE, WHEN NOT
  TO USE, an EXAMPLE, and a length floor. The gate is mechanical because review misses it.

One test caught a real CRLF bug in its own harness: `Path.read_text()` applies
universal-newline translation, so it turns `\r\n` into `\n` and would have hidden a
renderer that rewrote every line ending in the file. The fuzz cases read bytes.

---

## The wiring (`ronin.session`)

`task` is injected a `SubagentRunner`, and `ronin/session.py` is the module that
supplies the real one. It is the orchestrator seat in `docs/ARCHITECTURE.md` §0 and
the **only** module allowed to import the loop, the providers and the tools at once —
which is precisely why it exists: something has to introduce three layers that are
each written not to know about the others.

```
uv run python -m ronin.session_demo     # all three layers, offline
```

Five decisions live there, each a place where the obvious wiring is subtly wrong:

1. **The fast model, always.** `for_subagent()` takes no role argument.
2. **A subagent cannot spawn a subagent.** `task` is stripped from every child
   registry. Depth-1 is a limit, not an oversight: unbounded nesting is one request
   fanning out with no budget that sees the whole tree.
3. **A subagent cannot escalate to the user.** Its policy denies anything requiring
   approval. Forwarding would hang, or surface a prompt to a user who asked for
   something else three steps ago.
4. **A subagent gets its own `read_files`.** Sharing the parent's would mean a file the
   *child* read counts as "seen" for the parent's `write` guard — quietly weakening the
   one rule that prevents most destructive edits. There is a test for exactly this.
5. **Failure is a value.** A stalled, capped or misconfigured child returns text the
   parent can act on, labelled `[partial: …]` when the answer was cut off. Raising
   would kill the parent's turn over a child's problem. `CancelledError` is the one
   exception and propagates.

The demo found one more gap: only subagents were reaching the ledger, so the per-role
split showed a single row on a session where the main model had plainly just run.
`Session.record_turn` bills the parent, and the demo now prints both.

## Honest status

- ~~`task`'s runner is a stub outside tests.~~ **Done** — `ronin.session` wires it to a
  real nested `run_turn` on `router.for_subagent()`. See below.
- ~~`web_fetch`/`web_search` take injected callables and nothing supplies real ones.~~
  **Half done.** `tools/fetcher.py` is a real HTTP client on `http.client` + `socket` +
  `ssl`, wired in `cli/wire.py`, so `web_fetch` exists in a real session. It resolves the
  hostname, vets every address it answers with, and **connects to the vetted address**
  while keeping the name in the `Host` header and the TLS handshake — vetting a name and
  then dialling it asks DNS twice and trusts the second answer. Redirects are followed by
  hand so every hop is re-vetted. `web_search` is still absent and still needs a provider
  decision; it stays absent rather than existing and always erroring.
  - Two limits, on purpose: an HTTP proxy cannot be used (pinning an address and letting a
    proxy resolve the name are mutually exclusive, so `HTTPS_PROXY` is ignored — inject a
    `Fetcher` via `build_registry` in that environment), and a hostname the resolver cannot
    answer for is allowed through rather than refused.
- **`read` returns images as an artifact string**, not as a provider-native image block.
  The provider layer has no image-block rendering yet either (`Capabilities.vision` is
  declared and unused), so this is consistent but incomplete.
- **The working-directory boundary is not a sandbox.** `ToolContext.resolve` refuses
  paths outside the root after symlink resolution, which stops the accidental
  `../../etc/hosts`, and `bash` can walk out of it whenever it likes. The real boundary
  is the approval gate plus OS-level isolation, neither of which is this layer's job.
- **No import-graph test yet.** Nothing in `ronin/tools` imports `ronin/providers` or
  `ronin/core/loop` today, and that is still enforced by reading rather than by CI.
