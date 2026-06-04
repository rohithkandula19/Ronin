"""Write a Conventional Commit message from your diff — `ronin commit`.

ronin reads what changed, infers a sensible type + scope from the touched files
(so even the offline fallback is reasonable), and the agent drafts a clean
Conventional Commit subject + body grounded in the actual diff. The type/scope
inference and message assembly are pure and unit-tested; staging and committing
live in the command.
"""
from __future__ import annotations

from pathlib import Path

# evaluated in order — first matching predicate wins
_DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
_TEST_HINTS = ("test_", "_test.", "/tests/", "spec.")
_CHORE_NAMES = {"pyproject.toml", "package.json", "uv.lock", "poetry.lock",
                "requirements.txt", ".gitignore", "package-lock.json", "Makefile"}
_CI_PARTS = {".github", ".gitlab-ci.yml", ".circleci"}


def _is_test(path: str) -> bool:
    p = path.replace("\\", "/")
    return any(h in p for h in _TEST_HINTS)


def infer_type(paths: list[str]) -> str:
    """Infer a Conventional Commit type from the changed file paths. Pure.

    Heuristic, deliberately conservative: docs-only → docs, tests-only → test,
    CI-only → ci, manifest-only → chore; otherwise feat (the agent refines it)."""
    if not paths:
        return "chore"
    norm = [p.replace("\\", "/") for p in paths]
    if all(Path(p).suffix in _DOC_SUFFIXES for p in norm):
        return "docs"
    if all(_is_test(p) for p in norm):
        return "test"
    if all(any(c in p for c in _CI_PARTS) for p in norm):
        return "ci"
    if all(Path(p).name in _CHORE_NAMES for p in norm):
        return "chore"
    return "feat"


def guess_scope(paths: list[str]) -> str:
    """A scope from the common leading path segment, if the files agree. Pure."""
    if not paths:
        return ""
    segs = [p.replace("\\", "/").split("/") for p in paths]
    # skip common monorepo wrappers to reach a meaningful name
    skip = {"packages", "apps", "src", "lib"}
    candidates = []
    for parts in segs:
        meaningful = [s for s in parts[:-1] if s not in skip]
        candidates.append(meaningful[0] if meaningful else "")
    first = candidates[0]
    if first and all(c == first for c in candidates):
        return first
    return ""


def fallback_subject(paths: list[str]) -> str:
    """A reasonable Conventional Commit subject without an LLM. Pure."""
    typ = infer_type(paths)
    scope = guess_scope(paths)
    head = f"{typ}({scope})" if scope else typ
    if len(paths) == 1:
        what = Path(paths[0]).name
    else:
        what = f"{len(paths)} files"
    return f"{head}: update {what}"


def commit_prompt(summary: str, patch: str, inferred_type: str, *, max_chars: int = 18000) -> str:
    clipped = patch[:max_chars]
    note = "" if len(patch) <= max_chars else "\n\n[diff truncated]"
    return (
        "Write a Conventional Commit message for this staged diff. Output ONLY the "
        "message — a subject line `type(scope): summary` (<=72 chars, imperative "
        "mood, lowercase summary), then a blank line, then 1-4 bullet points of "
        "what changed and why. No code fences, no preamble. Choose the type from "
        f"the diff (inferred default: {inferred_type}).\n\n"
        f"Files: {summary}\n\n```diff\n{clipped}\n```{note}"
    )
