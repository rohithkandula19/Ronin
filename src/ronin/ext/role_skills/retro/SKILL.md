---
name: retro
description: Turn a session's lessons into durable notes by appending them to RONIN.md, the project memory file.
allowed-tools: [read, glob, edit, write]
adapted-from: gstack/retro
license: MIT
---
# retro — make the session teach the next one

A lesson learned and left in the transcript is lost at the next compaction. This skill
writes what was learned into `RONIN.md`, the project memory Ronin loads on every future
session, so the same wall is not hit twice.

1. **Mine the session for durable lessons.** Look for the things that would have saved
   time if known at the start, and keep only what is still true next week:
   - a convention or invariant this codebase enforces that was not obvious,
   - the command that actually runs the tests / lint / build,
   - a trap that cost real time, and the shape that avoids it,
   - a decision made and the reason, so it is not relitigated.
   Skip the one-off and the merely narrated — memory that fills with noise stops being
   read.

2. **Find the memory file.** `glob "RONIN.md"` (and check any nearer one for the
   subtree you worked in — the closer file wins at load time). If none exists, you will
   create one at the project root.

3. **Read before writing.** `read` the current `RONIN.md` so you extend it rather than
   clobber it, and so you do not add a note that is already there or now contradicts one
   that is.

4. **Write the lessons in.** Each entry is one or two lines, imperative and specific —
   "run `uv run pytest tests/ext -q`, not bare `pytest`," not "testing is important."
   Group them under a clear heading. Use `edit` to append to an existing file (match a
   unique anchor near the end); use `write` only to create the file when there is none.

5. **Keep it tight.** If a section has grown stale or redundant, prune it in the same
   pass. Then show the user the exact diff you made to their memory — it is their file,
   and they should see what their agent will remember.
