"""Tests for commit_msg — type/scope inference + message assembly."""
from __future__ import annotations

from ronin_cli.commit_msg import (
    commit_prompt,
    fallback_subject,
    guess_scope,
    infer_type,
)


def test_infer_docs_only() -> None:
    assert infer_type(["README.md", "docs/guide.md"]) == "docs"


def test_infer_tests_only() -> None:
    assert infer_type(["pkg/tests/test_a.py", "pkg/b_test.py"]) == "test"


def test_infer_chore_manifest() -> None:
    assert infer_type(["pyproject.toml", "uv.lock"]) == "chore"


def test_infer_ci() -> None:
    assert infer_type([".github/workflows/ci.yml"]) == "ci"


def test_infer_feat_for_mixed_code() -> None:
    assert infer_type(["src/a.py", "src/b.py"]) == "feat"


def test_infer_empty_is_chore() -> None:
    assert infer_type([]) == "chore"


def test_guess_scope_agreement() -> None:
    # packages/apps/src/lib are skipped to reach a meaningful name
    assert guess_scope(["packages/cli/src/x.py", "packages/cli/src/y.py"]) == "cli"


def test_guess_scope_disagreement_empty() -> None:
    assert guess_scope(["cli/a.py", "core/b.py"]) == ""


def test_fallback_subject_single_file() -> None:
    s = fallback_subject(["packages/cli/src/ronin_cli/foo.py"])
    assert s.startswith("feat(cli):") and "foo.py" in s


def test_fallback_subject_many_docs() -> None:
    s = fallback_subject(["docs/a.md", "docs/b.md"])
    assert s.startswith("docs(docs):") and "2 files" in s


def test_commit_prompt_grounds_in_diff() -> None:
    p = commit_prompt("2 files", "diff --git a/x b/x\n+hi\n", "feat")
    assert "Conventional Commit" in p and "+hi" in p and "feat" in p
