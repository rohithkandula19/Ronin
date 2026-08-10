# orient me in this repository

You are looking at a repository I have never read. Produce the orientation I would want
from the person who wrote it, and produce it from **evidence in the tree**, not from what
repositories of this shape usually look like.

Every claim you make must carry a `file:line` or a `path/`. If you cannot point at
something, do not say it — say you could not find it and name where you looked. An
orientation that is 90% right and unattributed is worse than one that is 70% right and
checkable, because I cannot tell which 10% to distrust.

Work top-down and stop when the budget runs out rather than skipping a section:

1. **What this is.** One paragraph: what it does, for whom, and how you know. Cite the
   README, the package metadata, or the entry point — in that order of preference, and
   say which you used. A README makes claims; an entry point is evidence.

2. **How to run it.** The actual commands, from `pyproject.toml` /
   `package.json` / `Makefile` / `justfile` / CI config — whichever exists. Include
   install, test and lint. Prefer what CI runs to what the README says: CI is executed,
   documentation is aspirational.

3. **The shape.** The top-level directories that hold real code, what each is for, and
   which one to read first. Say how you decided "real code" — file counts and imports,
   not intuition. Name anything that looks like it matters and is not code: a schema, a
   config format, a code-generation step.

4. **The spine.** Trace one complete path through the system: the entry point, the two
   or three modules a request passes through, and where it ends. Cite each hop. This
   section is the whole point of the exercise — a directory listing I can get myself.

5. **The rules of the house.** Whatever this codebase enforces that a newcomer would
   violate on day one. Look for: a layering or dependency rule (often a test that
   asserts it), a lint or type-check configuration, a naming convention that is
   load-bearing, a file that says "do not edit by hand", a CONTRIBUTING file. Cite the
   file that enforces each one, and say whether it is enforced or merely written down.

6. **Where the bodies are buried.** The three or four places a newcomer will lose a day.
   Look for: the longest files, the widest imports, `TODO`/`FIXME`/`XXX`/`HACK`, tests
   that are skipped or xfailed, a module whose comments explain a past incident. Cite
   each.

7. **What I would ask the author.** Three questions the code cannot answer — a design
   decision with no recorded reason, an abstraction whose purpose is unclear, a piece
   that looks unfinished. These are more valuable than another paragraph of summary.

Two things to avoid, because they are the failure modes of this task:

* **Do not describe the directory listing back to me.** "There is a `src/` directory
  containing the source" is not orientation.
* **Do not pattern-match.** If it is a Python package with a `providers/` directory, do
  not tell me what a providers directory usually contains — read one and tell me what
  *this* one contains.

If you run out of budget, stop mid-section and say which sections you did not reach.
An honest partial orientation is usable; a complete-looking one with a fabricated
section is not.
