---
name: qa
description: Drive a QA checklist against a running app or URL, probing endpoints and reporting what actually happened.
allowed-tools: [bash, web_fetch, read, todo_write]
adapted-from: gstack/qa
license: MIT
---
# qa — check what it does, not what it should do

QA is the discipline of exercising the thing and reporting observations, not
restating intended behaviour. Build the checklist with `todo_write` and work it item
by item, recording the actual result of each.

1. **Establish the target.** Get the URL or the command that starts the app. If a dev
   server is needed, start it with `bash` (background it so the shell stays free), then
   confirm it is up before testing — poll the health or index route, do not assume.

2. **Derive the checklist from the change.** `read` the diff or the spec and turn each
   claimed behaviour into a concrete check with an expected result: the happy path, the
   obvious error paths, empty and boundary inputs, and anything the change explicitly
   touched.

3. **Exercise it.**
   - For HTTP surfaces, `web_fetch` a URL to see a page as fetched, or use `bash` with
     `curl` when you need to set a method, header, body, or status code and read it back.
   - For a CLI or library, drive it through `bash` with real arguments.
   - Test the failure paths on purpose — a bad token, a missing field, a malformed body —
     and record the status and message, not just "it errored."

4. **Record honestly.** For each item: pass, fail, or blocked, with the exact
   observation — status code, message, or output. A fail names what you did, what you
   expected, and what you got, so it is reproducible.

5. **Know the edges of what Ronin can do here.** It cannot see rendered pixels, click a
   button, or judge visual layout — `web_fetch` returns extracted content, not a
   screenshot. Verify everything reachable over HTTP or the shell, and for the rest hand
   the human a tight manual checklist rather than claiming a pass you could not observe.
