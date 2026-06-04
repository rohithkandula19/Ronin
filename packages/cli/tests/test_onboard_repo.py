"""Tests for onboard_repo — git-url detection, fact gathering, prompt."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.onboard_repo import (
    facts_brief,
    gather_facts,
    is_git_url,
    onboard_prompt,
)


@pytest.mark.parametrize("s,ok", [
    ("https://github.com/x/y", True),
    ("https://github.com/x/y.git", True),
    ("git@github.com:x/y.git", True),
    ("ssh://git@host/x.git", True),
    ("./local/path", False),
    ("/abs/path", False),
    ("my-repo", False),
])
def test_is_git_url(s: str, ok: bool) -> None:
    assert is_git_url(s) is ok


def test_gather_facts_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[project.scripts]\nmycli = "x.main:app"\n',
        encoding="utf-8")
    (tmp_path / "README.md").write_text("# x", encoding="utf-8")
    pkg = tmp_path / "x"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "main.py").write_text("import x.util\n", encoding="utf-8")
    (pkg / "util.py").write_text("x = 1\n", encoding="utf-8")

    facts = gather_facts(tmp_path)
    assert "python" in facts["stack"]
    assert "mycli" in facts["entry_points"]
    assert "README.md" in facts["notable"]
    assert "pyproject.toml" in facts["notable"]


def test_facts_brief_and_prompt() -> None:
    facts = {"stack": ["python"], "package": "pkg", "modules": 12,
             "core_modules": ["util"], "entry_points": ["cli"],
             "notable": ["README.md"]}
    brief = facts_brief(facts)
    assert "python" in brief and "pkg" in brief and "cli" in brief
    p = onboard_prompt(facts)
    assert "onboarding" in p.lower() and "README.md" in p
    assert "Where to start" in p


def test_facts_brief_minimal() -> None:
    # no package / entries — still produces a stack line, no crash
    assert "Stack:" in facts_brief({"stack": ["unknown"]})
