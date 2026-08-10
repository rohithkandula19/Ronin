# Positioning

The argument, not the feature list. If a paragraph here stops being true, the paragraph
changes — not the reader's interpretation of it.

## 1. What ronin is

Ronin is a terminal-native, provider-agnostic agentic coding harness that makes
open-weight models usable for real engineering work. Each word in that is load-bearing.
*Terminal-native* means the interface is a shell and a repository, not an editor pane, so
it composes with CI, with a container, and with a machine nobody is sitting at.
*Provider-agnostic* means the model is a config entry rather than an architectural
assumption — fifteen adapters today, and swapping Claude for Kimi or for weights on your
own disk is a TOML edit. *Agentic* means it reads, edits, runs and verifies across many
turns under an approval gate, rather than answering a question about code. And *makes
open-weight models usable* is the part that is a claim rather than a description: the
harness exists to close the gap between what a small open model does out of the box and
what an engineering task actually requires.

## 2. The wedge

**Ronin makes an open-weight model measurably better at real engineering work, and
publishes both the measurement and the improvement.** That is the one thing Claude Code,
Codex and Cursor structurally cannot do: their value is a frontier model they own and do
not let you replace, so improving *your* model is not a thing they are built to want. The
mechanism is a closed loop — the harness runs the task, an eval suite of 118 verified
tasks scores it, a six-class taxonomy says whether the failure was the model's or the
harness's, and the trajectories that passed become training data for the model the
harness runs on. Nobody else owns both ends of that loop, which is why nobody else can
run it. We commit to this framing over the weaker "run any model" version because
provider-agnosticism alone is table stakes — Aider, Cline and Continue all offer it, and
none of them close the loop back into the model. Being the only project that can hand you
a *better* open model, with the number to prove it, is a position; being one of four that
can talk to Ollama is not.

## 3. What ronin will not do

1. **Not a chatbot.** No conversational surface for its own sake. If a turn does not read,
   edit, run or verify something in a repository, it is out of scope.
2. **Not an IDE.** No editor, no autocomplete, no inline suggestions. Cursor wins that and
   should; competing there would cost years and lose.
3. **Not AGI.** No claim, no roadmap item, no implied trajectory. The word does not appear
   in the product. What looks like generality is memory, initiative and self-repair, and
   each of those is an engineering problem with a definite scope.
4. **Not a model lab.** No pretraining, ever. A frontier run is $5M at the absolute floor
   and $100M+ in practice; ronin builds *on* Apache-2.0 weights somebody else paid for,
   and post-trains adapters that cost a free Colab session.
5. **Not a benchmark-maximiser.** SWE-bench is a second external signal, deliberately not
   tuned on. When the honest number is bad, the honest number gets published; a suite you
   optimise against stops measuring anything the day you start.

## 4. Who switches

The user is a team that **cannot send its source code to a US frontier API** — and the
distinguishing feature is that this is not a preference they can be argued out of. Four
shapes of it: *regulated*, where a compliance regime names the data (health, defence,
finance, legal privilege); *air-gapped*, where there is no egress at all and the question
never reaches procurement; *non-US*, where a sovereignty rule or a data-residency
commitment excludes the vendors outright, which is most of the EU public sector and a
growing share of Asian enterprise; and *cost-capped*, where per-seat frontier pricing
across a large engineering org is simply refused. A privacy-strict startup rounds to the
same behaviour without the paperwork. What unites them is that they are already using a
weaker open model and feeling it, so the thing they want is not another wrapper around a
model they cannot call — it is for the model they *can* call to get better. Everyone else
has a frontier API and no reason to move, and courting them would blunt every decision
above.

## 5. The proof

One number decides this, and it has the same shape as the argument: **base
Qwen2.5-Coder-1.5B scores X% on a held-out slice of the 118-task suite; the ronin adapter
on the same base, in the same harness, at the same seeds, scores Y%.** Everything rests on
Y being meaningfully greater than X, because that single comparison is the whole claim
that a harness can lift an open model rather than merely host one. Its credibility comes
from the controls: the same harness both sides, so the delta is not a harness change; a
hold-out split **by task and never by example**, so it is not memorisation reported as
generalisation; and a suite where every task is proven to discriminate — bare fixture
fails, reference solution passes — so a score cannot be inflated by a task that always
passes. The secondary numbers are tool-syntax validity and recovery rate, because those
are what the adapter is actually trained for and where a small model most obviously fails
without help. **X and Y are unmeasured today.** No run has happened, and no number appears
anywhere in this repository until one does. If Y turns out to be less than X, that gets
published as the result — a negative finding a lab can trust is worth more than a positive
one it cannot.
