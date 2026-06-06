"""Tests for the API-to-plugin generator."""
from __future__ import annotations

import ast

import pytest

from ronin_cli.plugin_from_api import generate_api_plugin, url_params, valid_name


def test_url_params_extracts_placeholders() -> None:
    assert url_params("https://x.com/{user}/repos/{repo}?k={user}") == ["user", "repo"]
    assert url_params("https://x.com/all") == []


@pytest.mark.parametrize("n,ok", [("weather", True), ("get_x", True), ("Weather", False), ("class", False), ("2x", False)])
def test_valid_name(n, ok) -> None:
    assert valid_name(n) is ok


def test_generated_plugin_parses_and_loads(tmp_path) -> None:
    from ronin_cli.plugins import build_plugin_tools
    src = generate_api_plugin("dog_pic", "https://dog.ceo/api/breeds/image/random",
                              fields=["message", "status"], blurb="A random dog")
    ast.parse(src)                                   # valid python
    pdir = tmp_path / ".ronin" / "plugins"
    pdir.mkdir(parents=True)
    (pdir / "dog_pic.py").write_text(src, encoding="utf-8")
    tools = build_plugin_tools(tmp_path)             # loads through the agent loader
    assert [t.name for t in tools] == ["dog_pic"]


def test_generated_plugin_with_params_has_required() -> None:
    src = generate_api_plugin("gh_repo", "https://api.github.com/repos/{owner}/{repo}",
                              fields=["stargazers_count"])
    assert "def gh_repo(owner: str, repo: str)" in src
    assert '"required": ["owner", "repo"]' in src
    assert 'format(owner=owner, repo=repo)' in src


def test_generate_rejects_bad_name() -> None:
    with pytest.raises(ValueError):
        generate_api_plugin("Bad-Name", "https://x.com")
