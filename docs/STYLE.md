# voice

How Ronin's own words read. Not a preference — a spec, in the same sense as the
engineering rules in `RONIN.md`, because every string in `src/ronin/` is part of the
product and an interface that changes register between two screens reads as two
programs.

This document was written by sampling what is already there rather than by inventing
a house style. The voice below is the one the code already speaks; writing it down is
what stops the next hundred strings from drifting out of it.

> Note on scope: this describes `src/ronin/` — the `ronin` binary. The older
> `packages/cli/` tree (`ronin1`) has a different, warmer register with emoji and a
> mascot. The two are not compatible. New work follows this document.

## the shape of a sentence

**Lowercase first word.** Not a stylistic tic: the interface is a terminal, where
the surrounding text is lowercase, and a capitalized sentence in a status line reads
as shouting.

```
waiting for the model
queued — runs when this turn ends (esc to interrupt now)
no changes
```

**Terse, then the reason.** The house construction is two clauses joined by an em
dash, where the second says *why*. It is the difference between a message the user
obeys and a message the user understands.

```
retrying — connection reset
CompactionPolicy.pinned_tail_turns must be >= 1 — folding the turn in progress
leaves the model answering a question it cannot see
```

**No terminal period on a label or a fragment.** Full sentences take one.

```
/cost                show token and dollar spend for this session
/plan                switch to plan mode: read and reason, mutate nothing
```

**Say what to do next.** An error that names only the problem has done half the job.

```
src/main.py does not exist. use glob or ls to find the right path.
no checkpoint was taken, so /undo cannot roll this turn back: {detail} the turn
continues — everything else works normally.
```

**Name the caveat rather than rounding it away.** Estimates are labelled as
estimates, absences are labelled as absences, and a number nobody supplied is never
printed.

```
transcript now ~12,400 tokens (estimated at 4 chars/token)
(no branch)
(not installed; running from a source tree)
```

## words

| do | don't | why |
| --- | --- | --- |
| `cannot`, `does not`, `is not` | `can't`, `doesn't`, `isn't` | contractions read casual next to a traceback |
| `—` (em dash), `·` (middle dot) | `-`, `\|`, `>` as separators | the two house separators; both are lint-safe |
| `` `ronin doctor` `` | "run the doctor command" | a command the user can paste beats a description of one |
| "the turn was interrupted" | "Oops! Something went wrong" | no apology, no exclamation, no emoji |
| "nothing was proven by a test" | "verification skipped" | say the consequence, not the mechanism |

**No emoji, no exclamation marks, no greetings.** The design language calls the
interface "a dojo, not a cockpit". A tool that says *good morning* is a tool
performing friendliness instead of working.

**Never blame the user.** State what happened and what would fix it. "you forgot
to" has no place in it.

## what a failure sounds like

Failure text is the most-read text in the program, so it gets the most care. Three
rules, in order of how often they are broken:

1. **The reason leads.** A cut, a truncation or a head-biased render can lose the
   end of a message, so the part that cannot be lost goes first.
2. **The output comes with it.** `command exited 1` on its own tells the model and
   the user that something broke and nothing about what.
3. **Say what was removed.** Every truncation carries a marker naming the cut — see
   `RONIN.md`. Silence about a cut is what makes a diff or an approval prompt
   dangerous rather than merely ugly.

## colour and glyphs

**Colour is injected, never emitted.** A renderer asks an injected `Styles` map to
wrap a semantic token; it does not write an escape code. That is what lets the same
functions serve the Textual app, the line session and a headless run, and it is why
`render.py` has no third-party import.

**Colour is decoration, never the message.** Anything colour conveys must also be in
the text, because `NO_COLOR` is honoured and a pipe has no colour at all. `error: `
is a prefix, not a red tint.

**Glyphs are ASCII unless a Unicode one is clearer *and* unambiguous.** `ruff`'s
ambiguous-unicode rules are on with no ignore list, and legibility is the stricter
constraint anyway:

- safe and in use: `●` `→` `…` `⚠` `·` `—` `│` `☐` `◐` `☑`, the braille spinner, box
  drawing, and the block glyphs the wordmark is built from
- rejected: `×` (indistinguishable from a letter in most terminal fonts — the
  in-tree comment on `REPEAT_GLYPH` records this), `›`, en dash `–`, and any kaomoji
  containing `ʔ`

**A glyph that changes width is worse than no glyph.** The spinner is single-width
braille for exactly this reason.

## the wordmark

Restrained on purpose. The house aesthetic is ink on paper — "the deliberate
antithesis of the neon-gradient AI aesthetic" — so the wordmark is thin strokes with
a lot of empty space, it works with no colour, and it degrades to the word `ronin`
in a narrow terminal rather than wrapping.
