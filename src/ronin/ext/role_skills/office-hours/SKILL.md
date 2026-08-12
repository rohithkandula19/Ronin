---
name: office-hours
description: Force the clarifying questions out into the open and wait for answers before writing any code.
allowed-tools: [read, grep, glob]
adapted-from: gstack/office-hours
license: MIT
---
# office-hours — ask first, build second

The failure this prevents: you guess what the user meant, build the wrong thing
confidently, and the mistake is only caught after the work is done. Use it whenever a
request has more than one reasonable reading. While this skill drives you may read and
search to inform your questions, but you may **not** write, edit, or run commands that
change anything.

1. **Read the request literally**, then read it adversarially: where could a
   well-meaning engineer build something the user did not want and still claim they
   followed instructions? Each such spot is an ambiguity.

2. **Ground the questions in the code.** Use `grep`, `glob`, and `read` to check what
   already exists before you ask — a question the repository already answers wastes the
   user's turn. Ask about what the code cannot tell you: intent, scope, priorities,
   the shape of "done."

3. **Enumerate the ambiguities** as a numbered list. For each, state the decision to be
   made and the options you can see, with your recommended default in **bold** and one
   line on why. A question with a default is answerable in five seconds; an open-ended
   one stalls.

4. **Cover the axes that bite most often:** scope (what is in and explicitly out),
   inputs and their edge cases, the expected output or interface, error and failure
   behaviour, performance or scale constraints, and which existing code this must match.

5. **Ask, then stop.** Post the numbered questions and wait. Do not answer them
   yourself and proceed. When the replies come back, restate the now-settled decisions
   in one short block so the user can catch a misread before code exists — then hand off
   to `autoplan` or start building.
