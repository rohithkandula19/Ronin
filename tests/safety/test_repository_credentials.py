"""The repository-wide credential gate: no key-shaped literal anywhere in the tree.

Ported from ``packages/cli/tests/test_cov_secrets.py``, which built it on v1's
``ronin_cli.secret_scan.scan_repo``. That module is scheduled for deletion along
with the rest of ``packages/cli``, and the gate must not go with it — it is the
check that caught a credential-shaped literal in this repository's own test
fixtures twice while the v2 scanner was being written, both times before the
commit reached anyone else.

Rebuilt on the v2 scanner (:func:`ronin.cli.scan.walk_tree` +
:func:`ronin.safety.credentials.find_secrets`), so it now depends only on the
tree that survives.

Two properties, not one. The obvious property is that the repository is clean.
The property that keeps the first one honest is that this gate *can fail* — a
whole-tree scan is exactly the shape of test that passes forever once its walk
quietly stops reaching files, and it looks identical to success while doing it.
That is the same failure the v1 scanner shipped, where a tree it could not read
was reported clean; ``ronin scan`` grew a distinct exit code for it precisely
because "found nothing" and "looked at nothing" are different answers.

Offline by construction: reads files, talks to nothing.
"""

from __future__ import annotations

from pathlib import Path

from ronin.cli.scan import walk_tree
from ronin.safety.credentials import Finding, find_secrets

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# A floor, not a count. The point is to notice a walk that has stopped walking,
# so it sits far below the real number (thousands) and needs no maintenance when
# files come and go.
FEWEST_CREDIBLE_FILES = 200


def scan_tree(root: Path) -> tuple[list[Finding], int]:
    """Every finding under ``root``, and how many files were actually read."""
    findings: list[Finding] = []
    seen = 0
    for relative_path, text in walk_tree(root):
        seen += 1
        findings.extend(find_secrets(text, relative_path))
    return findings, seen


def describe(findings: list[Finding]) -> str:
    """Locations and kinds — never values. Reporting the hit must not leak it."""
    return "\n".join(f"  {finding.path}:{finding.line}  {finding.kind}" for finding in findings)


def test_repository_has_no_literal_credentials() -> None:
    findings, seen = scan_tree(REPOSITORY_ROOT)
    assert not findings, (
        f"{len(findings)} credential-shaped literal(s) in the tree "
        f"(scanned {seen} files):\n{describe(findings)}"
    )


def test_the_walk_actually_reached_the_tree() -> None:
    """A clean result from an empty walk is not a clean repository.

    Without this, pruning every directory — a bad entry in ``SKIP_DIRS``, a
    stray ``.gitignore`` rule, a root that resolved somewhere unexpected — would
    turn the gate above into a test that cannot fail, silently and permanently.
    """
    _, seen = scan_tree(REPOSITORY_ROOT)
    assert seen >= FEWEST_CREDIBLE_FILES, (
        f"walked only {seen} files under {REPOSITORY_ROOT}; the gate above would "
        f"be passing on an empty scan"
    )


def test_the_gate_can_fail(tmp_path: Path) -> None:
    """Proof of teeth: the same machinery, pointed at a tree with a key in it.

    Assembled at run time rather than written as a literal, for the reason
    ``test_safety_credentials.key`` gives at length — a synthetic key committed
    to this repository is indistinguishable, to every scanner that reads it,
    from a real one.
    """
    planted = "ghp_" + "a" * 36
    (tmp_path / "leaky.py").write_text(f'TOKEN = "{planted}"\n', encoding="utf-8")
    (tmp_path / "clean.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    findings, seen = scan_tree(tmp_path)

    assert seen == 2, f"expected to read both files, read {seen}"
    assert len(findings) == 1, describe(findings)
    assert findings[0].path == "leaky.py"
    assert planted not in describe(findings), "the report disclosed the value it found"
