# ronin — Demo Script

A ~2-minute terminal walkthrough. Every command below is real and verified against v1.0.0-rc.2. Record with [`vhs`](https://github.com/charmbracelet/vhs) (`docs/demo/demo.tape` drives the existing GIF) or asciinema.

## 0. Setup (once)

```bash
curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
ronin --version          # ronin 1.0.0-rc.2 (<sha>, <branch>)
```

## 1. Free-first, no credit card (~20s)

```bash
ronin                    # first run: free-first onboarding — pick Gemini/Groq/Cerebras/OpenRouter/Ollama
# in-session:
/provider                # every provider · free/paid · which have a key
/free on                 # jump to a $0 provider you can run right now
```

Talking point: *same agent, same UI, your choice of brain — it defaults to free.*

## 2. The coding agent (~40s)

```bash
ronin code "add a --json flag to the CLI and update the tests"
```

Show: streaming reply, the live plan tracker, a syntax-highlighted diff, and the **approval gate** (`y/N`) before any file write or shell command. Point at the chip strip: `[FREE] [cerebras:gpt-oss-120b] [normal] [main*] [write-gated]`.

## 3. Roles (~15s)

```bash
# in-session
/role reviewer           # read-only — reviews the diff, cannot edit
/role debugger           # then: "why is this test failing?"
```

Talking point: *read-only roles are enforced, not just suggested.*

## 4. The verification pipeline (~30s)

```bash
ronin pipeline "add CSV export" --dry-run
```

Show the plan: **architect → implementer → reviewer → tester → verifier**, per-stage permissions, read-only vs write-capable. Then the real thing (talk over it, don't wait):

```bash
ronin pipeline "fix failing auth tests" --write --auto-verify-all
```

Talking point: *evidence-based — it captures the real diff, runs required/optional test suites through the gate, and produces a Final Verification truth table; a commit only happens after a passing verdict, and only with your approval.*

## 5. Offline + the arcade (~15s)

```bash
ronin --offline "explain @main.py"    # zero network egress, local brain
ronin play                            # 31 free terminal games — take a break
```

## Close

*Provider-agnostic, terminal-native, free-first, safety-gated. 3,274 offline tests. MIT.*
