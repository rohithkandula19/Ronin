# ronin — Quickstart 🐼

The coding agent that runs **free, local, and air-gapped**. Any provider — Claude for top quality, free tiers on Gemini / Cerebras / Groq, or a fully local model with zero keys — with **no telemetry** and a **hard safety floor** under destructive commands.

## 1. Install & log in

```bash
curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
ronin                      # then, in-session:
/login cerebras            # paste a free key (or: anthropic, gemini, groq, …)
```

## 2. Code (the Claude-Code experience)

```bash
ronin code "add a --json flag and update the tests"
ronin code "explain @main.py and fix the bug in @utils.py"   # @-mention files / @-URLs
ronin code --plan "refactor the auth module"                 # plan → approve → execute
```
Every edit shows a diff and waits for your `y/N`. Shift+Tab cycles **normal → auto-accept → plan**.

## 3. Turn on the Cost Router (free where it can be)

```bash
ronin setup                # suggests route_fast/route_strong from your keys
# now every session footer shows: 💰 cost $X · saved $Y vs all-anthropic · N/M turns free
ronin router               # see what it's learned about each provider in this repo
ronin costs                # lifetime savings
```

## 4. The provider-agnostic superpowers

```bash
ronin consensus "should we use a queue or a cron here?" -m anthropic,gemini,cerebras
ronin dojo "add retry to the http client" -m anthropic,gemini,cerebras   # rivals compete; judge picks
ronin duel --against gemini          # a rival vendor red-teams your git diff
ronin kaizen                         # ronin fixes its OWN source, proven by the test suite
```

## 5. The swarm — a cross-vendor *team* on one task 🐝

```bash
ronin swarm "add caching to the API client" \
  -r architect=anthropic,implementer=cerebras,reviewer=gemini
```
Architect plans · implementer codes · reviewer critiques · implementer revises — each role on a different vendor's model.

## 6. Nightshift — autonomous, while you sleep 🌙

```bash
# work the backlog once (dry-run first):
ronin nightshift --issues
ronin nightshift --issues --execute --duel openai --budget 5

# or schedule it nightly, with a swarm per task:
ronin nightshift --schedule "0 2 * * *" --issues \
  --swarm architect=anthropic,implementer=cerebras,reviewer=gemini \
  --duel openai --budget 5

# in the morning:
ronin patches                 # review what it did
ronin patches --apply --clean # apply the test-passing, un-flagged patches
```
Each task runs in an isolated worktree → implemented → **proven against your tests** → cross-vendor reviewed → saved as a reviewable patch. Never pushes, never touches your tree, stops at the budget.

## 7. Trust, safety & your own model

```bash
ronin --sentinel code "..."   # abstains over bluffing (CONFIDENCE: high/med/low)
ronin scan --staged           # block secrets; `ronin hook install` for a pre-commit guard
ronin --budget 0.50 code "…"  # spend cap
ronin style --out me.jsonl    # export your sessions as a fine-tuning dataset (free model, your style)
```

## Handy

```bash
ronin recall "auth bug"   # search past sessions   ·   ronin replay <id>   ·   ronin export
ronin map --write         # architecture overview → RONIN.md
ronin review --pr 42 --comment   ·   ronin triage   ·   ronin changelog   ·   ronin pr
ronin config              # view/set provider · model · routing · budget · sentinel
ronin --offline           # local brain, zero network egress
```

> The binary is `ronin` (`ro` also works). Config lives in `.ronin/` (project) and `~/.config/ronin/`.
