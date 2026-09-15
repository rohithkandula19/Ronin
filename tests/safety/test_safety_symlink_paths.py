"""A symlink is a second name for a file, and the deny lanes only knew the first.

`Denylist.resolve` is symlink-blind on purpose — it reads bash *command text*, where
the path may not exist yet and touching the disk would answer a question nobody
asked. That reasoning was then applied to a place it does not fit: a file tool's
``path=`` argument, where the tool is about to open exactly that path and
``ToolContext.resolve`` is about to follow exactly that link.

So both lanes judged the name the model typed. ``notes.txt -> .env`` was judged as
``notes.txt``; a rule on ``config/**`` did not match ``harmless.txt``; and
``docs -> .git`` made ``docs/config`` a name for ``.git/config`` — a write there runs
commands on the next git invocation, and under ``auto_edit`` there is no human in the
path at all. Confinement did not help: every target is *inside* the tree, which is
precisely what confinement checks.

The tests run in both directions. Half of them are that the links are now caught;
the other half are that ordinary paths, missing paths and the command-text lane are
exactly as they were — an over-fix here would refuse the everyday case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ronin.core.types import DangerLevel, Mode, ToolSpec, ToolUse
from ronin.safety.denylist import Denylist, link_target
from ronin.safety.policy import Decision, PathGlob, PolicyEngine, Rule, RuleSet

WRITE = ToolSpec(
    name="write", description="w", danger_level=DangerLevel.MUTATING, requires_approval=True
)
READ = ToolSpec(name="read", description="r")


def repo(root: Path) -> Path:
    """A workspace whose links all point at something worth protecting."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    (root / "deploy").mkdir(exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / ".env").write_text("OPENAI_API_KEY=sk-real\n", encoding="utf-8")
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (root / "config" / "prod.yaml").write_text("db: prod\n", encoding="utf-8")
    # Not a PEM header — the repository's own secret scanner matches that shape
    # anywhere in the tree and cannot tell a fixture from a real leak, which is the
    # right call. `key_material_read` classifies by filename, so contents never
    # mattered here.
    (root / "deploy" / "id_rsa").write_text("KEY-MATERIAL-SENTINEL\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "notes.txt").symlink_to(".env")
    (root / "harmless.txt").symlink_to("config/prod.yaml")
    (root / "readme.txt").symlink_to("deploy/id_rsa")
    (root / "docs").symlink_to(".git")
    return root


def engine(root: Path, *, mode: Mode = Mode.ASK, rules: tuple[Rule, ...] = ()) -> PolicyEngine:
    return PolicyEngine(
        rules=RuleSet(rules=rules),
        denylist=Denylist(workspace_root=root, home=root / "home"),
        mode=mode,
    )


def decide(eng: PolicyEngine, spec: ToolSpec, path: str) -> Decision:
    return eng.evaluate(spec, ToolUse(id="x", name=spec.name, arguments={"path": path})).decision


DENY_GIT = (
    Rule(
        tool="write", matcher=PathGlob(pattern=".git/**"), decision=Decision.DENY, source="project"
    ),
)
DENY_CONFIG = (
    Rule(
        tool="write",
        matcher=PathGlob(pattern="config/**"),
        decision=Decision.DENY,
        source="project",
    ),
)


# --------------------------------------------------------------------------- #
# where the path really goes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("word", ["src/app.py", "README.md", "does/not/exist.py", "", "src"])
def test_an_ordinary_path_reports_no_relocation(tmp_path: Path, word: str) -> None:
    """The common case has to stay free, including for files that do not exist yet."""
    assert link_target(word, repo(tmp_path)) is None


def test_a_link_reports_what_it_points_at(tmp_path: Path) -> None:
    assert link_target("notes.txt", repo(tmp_path)) == ".env"


def test_a_link_in_an_ancestor_counts_too(tmp_path: Path) -> None:
    """``docs/config`` is not itself a link. It is still a name for ``.git/config``."""
    assert link_target("docs/config", repo(tmp_path)) == ".git/config"


def test_a_workspace_reached_through_a_link_does_not_relocate_everything(tmp_path: Path) -> None:
    """The macOS shape: ``/tmp`` is ``/private/tmp`` and a tmp_path hides behind a link.

    Comparing a resolved path against an unresolved root reports *every* path as
    relocated, and every deny rule starts matching things it never named.
    """
    real = repo(tmp_path / "real")
    through = tmp_path / "through"
    through.symlink_to(real)
    assert link_target("src/app.py", through) is None
    assert link_target("notes.txt", through) == ".env"


def test_a_broken_link_does_not_raise(tmp_path: Path) -> None:
    """A safety check that dies on a dangling link is a session that dies on one."""
    root = repo(tmp_path)
    (root / "dangling").symlink_to("nowhere-at-all")
    assert link_target("dangling", root) == "nowhere-at-all"


def test_a_link_out_of_the_tree_is_named_absolutely(tmp_path: Path) -> None:
    root = repo(tmp_path / "workspace")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (root / "escape.txt").symlink_to(outside)
    assert link_target("escape.txt", root) == str(outside.resolve())


# --------------------------------------------------------------------------- #
# a rule the user wrote follows the link
# --------------------------------------------------------------------------- #


def test_a_rule_on_the_target_matches_the_link(tmp_path: Path) -> None:
    eng = engine(repo(tmp_path), rules=DENY_CONFIG)
    assert decide(eng, WRITE, "config/prod.yaml") is Decision.DENY
    assert decide(eng, WRITE, "harmless.txt") is Decision.DENY


def test_a_rule_on_a_directory_matches_through_a_linked_directory(tmp_path: Path) -> None:
    """The worst ending: a write to ``.git/config`` runs commands on the next git call."""
    eng = engine(repo(tmp_path), rules=DENY_GIT)
    assert decide(eng, WRITE, "docs/config") is Decision.DENY


@pytest.mark.parametrize("mode", [Mode.ASK, Mode.AUTO_EDIT, Mode.FULL])
def test_no_mode_lets_the_link_through(tmp_path: Path, mode: Mode) -> None:
    """In auto_edit and full nobody is asked, so the rule is the only thing standing there."""
    eng = engine(repo(tmp_path), mode=mode, rules=DENY_GIT)
    assert decide(eng, WRITE, "docs/config") is Decision.DENY


def test_the_literal_spelling_still_matches_its_own_rule(tmp_path: Path) -> None:
    """Added to the candidates, never substituted for them."""
    eng = engine(repo(tmp_path), rules=DENY_GIT)
    assert decide(eng, WRITE, ".git/config") is Decision.DENY


# --------------------------------------------------------------------------- #
# the unconditional list follows it too
# --------------------------------------------------------------------------- #


def test_a_secret_file_is_refused_under_its_other_name(tmp_path: Path) -> None:
    eng = engine(repo(tmp_path), mode=Mode.FULL)
    assert decide(eng, WRITE, ".env") is Decision.DENY
    assert decide(eng, WRITE, "notes.txt") is Decision.DENY


def test_key_material_is_refused_under_its_other_name(tmp_path: Path) -> None:
    eng = engine(repo(tmp_path), mode=Mode.FULL)
    verdict = eng.evaluate(READ, ToolUse(id="x", name="read", arguments={"path": "readme.txt"}))
    assert verdict.decision is Decision.DENY
    assert [hit.code.value for hit in verdict.deny_hits] == ["key_material_read"]


# --------------------------------------------------------------------------- #
# and nothing else changed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ["src/app.py", "NEW_FILE.md", "src/sub/deep.py"])
def test_an_ordinary_write_is_still_allowed(tmp_path: Path, path: str) -> None:
    eng = engine(repo(tmp_path), mode=Mode.AUTO_EDIT, rules=DENY_GIT)
    assert decide(eng, WRITE, path) is Decision.ALLOW


def test_the_command_text_lane_stays_symlink_blind(tmp_path: Path) -> None:
    """Deliberate, and documented: that lane judges a command that has not run.

    The path may not exist yet, and reading the disk to decide whether `rm ./link` is
    safe would be answering a question about a different moment in time.
    """
    root = repo(tmp_path)
    denylist = Denylist(workspace_root=root, home=root / "home")
    assert denylist.resolve("notes.txt", root) == root / "notes.txt"


def test_a_denylist_free_engine_does_not_try_to_resolve(tmp_path: Path) -> None:
    """Without a denylist there is no workspace root, and a wrong root is worse than none."""
    eng = PolicyEngine(rules=RuleSet(rules=DENY_CONFIG), mode=Mode.FULL)
    assert decide(eng, WRITE, "harmless.txt") is not Decision.DENY


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
