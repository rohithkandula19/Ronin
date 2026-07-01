# The verification pipeline

`ronin pipeline "<task>"` runs the coding roles **in sequence** with gated handoffs and evidence-based verification. It is **sequential, one agent per stage — not parallel and not autonomous**.

```bash
ronin pipeline "add CSV export" --dry-run          # preview: plan + permissions, nothing runs
ronin pipeline "add CSV export" --write            # gated edits, then verify
```

## Stages (default)

**architect → implementer → reviewer → tester → verifier**

Each stage wears a Wave-3 role and hands the next a **typed artifact** (not just prose): `ArchitectPlan` (objective, files, steps, risks, acceptance criteria), `ImplementationReport`, `ReviewReport`, `VerificationReport`. Read-only roles (architect/reviewer/verifier) are *enforced*. Customize with `--roles a,b,c`.

## Evidence & verification

- **Real diff evidence** — captures the actual `git diff HEAD` (tracked **and** new untracked files, read-only) so verification reasons about real changes, not the implementer's self-report. `--diff-context`, `--max-diff-bytes`, `--no-diff-evidence`.
- **Independent verification** — `--verify-cmd "<cmd>"` (or auto-detected) runs your tests through the approval gate and reconciles the real exit code. A tester that claimed "passed" but the command fails → **failed**.
- **Multi-suite, required vs optional** — `--verify-suite "unit:pytest -q"`, mark optional with `?` (`"lint?:ruff check ."`) or `--required-suite` / `--optional-suite`, or `--auto-verify-all` (tests/build required; lint/typecheck optional). A required failure fails; an optional failure only **warns**.
- **Contract checks** — cross-check the artifacts (changed files vs plan, acceptance-criteria coverage, unresolved review blockers).
- **`--semantic-contract`** — a read-only model pass judging whether the **actual diff** fulfils the plan. Never claims a pass when the diff is missing (→ unknown) or truncated (→ warning).

## Final verdict & truth table

A safety-first combined verdict (**passed / failed / blocked / unknown**) and a compact **Final Verification** truth table: diff-evidence · untracked-evidence · suites (required/optional) · verify-result · git-snapshot · checkpoint-restore · acceptance · contract · semantic · review-blockers · final verdict. Exit is non-zero on failed/blocked.

## Gated finish

- `--commit` offers a commit **only after a passing verdict** (else an explicit `y/N`), diff summary shown first. `--pr` then offers a gated push + PR. `--branch` / `--commit-message` / `--pr-title` / `--pr-body` override drafts.

## Resume & checkpoints

- `--save-state <file>` checkpoints `PipelineState` (with a git snapshot) after every stage; `--resume <file>` continues from the first incomplete stage.
- If the tree moved, resume **refuses** unless `--force-resume`. `--checkpoint` takes a safety snapshot first; `--list-checkpoints`, `--restore-latest-checkpoint`, `--restore-checkpoint-id <n>`, `--restore-checkpoint-interactive` restore it — always **gated**, re-checked afterward, never destroying local work silently.

## Free / offline / json

`--free` and `--offline` keep it $0; `--json` / `--out <file>` emit the full state including all evidence.
