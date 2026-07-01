"""RC version alignment: __version__ is valid PEP 440 and maps to the display form
used by the UI / git tag."""
from __future__ import annotations

import re

from ronin_cli import __version__, display_version


def test_package_version_is_valid_pep440() -> None:
    # release or pre-release (aN/bN/rcN); no dashes/underscores in the package version
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", __version__), __version__


def test_display_version_maps_rc() -> None:
    assert display_version("1.0.0rc1") == "1.0.0-rc.1"
    assert display_version("1.0.0b2") == "1.0.0-b.2"
    assert display_version("2.3.4a5") == "2.3.4-a.5"
    assert display_version("1.2.3") == "1.2.3"       # final release unchanged


def test_display_matches_git_tag_form() -> None:
    # the current package version's display form is what a v<tag> would use
    disp = display_version(__version__)
    assert disp.replace("-", "").replace(".", "") == __version__.replace(".", "")
    # for the RC, the friendly form is 1.0.0-rc.2
    if __version__ == "1.0.0rc2":
        assert disp == "1.0.0-rc.2"


def test_pyproject_version_matches_dunder() -> None:
    import pathlib
    import tomllib
    root = pathlib.Path(__file__).resolve().parents[1]  # packages/cli
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__
