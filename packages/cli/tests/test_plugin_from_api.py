"""Tests for the API-to-plugin generator."""
from __future__ import annotations

import ast

import pytest

from ronin_cli.plugin_from_api import generate_api_plugin, url_params, valid_name


# The trust gate (plugin_trust) refuses to IMPORT a plugin file that was not
# explicitly trusted: `.ronin/plugins/` lives in the repo, so importing it is RCE
# (see test_plugin_trust.py). build_plugin_tools deliberately offers NO bypass, so
# these tests trust their fixture exactly as a real user's `ronin plugin
# new/add/from-api` does (those auto-trust on write).
@pytest.fixture(autouse=True)
def _isolated_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("RONIN_HOME", str(tmp_path / "_trust_home"))


def _trusted_build(root):
    """Trust every plugin in <root>/.ronin/plugins, then load via the agent path."""
    from ronin_cli.plugin_trust import trust
    from ronin_cli.plugins import build_plugin_tools as _b
    pdir = root / ".ronin" / "plugins"
    if pdir.is_dir():
        for f in sorted(pdir.glob("*.py")):
            if not f.name.startswith("_"):
                trust(f)
    return _b(root)


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
    tools = _trusted_build(tmp_path)             # loads through the agent loader
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
