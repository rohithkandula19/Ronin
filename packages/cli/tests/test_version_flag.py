"""v1.0 release: `ronin --version` and `ronin version` both report the version."""
from __future__ import annotations

from typer.testing import CliRunner

from ronin_cli import __version__, display_version
from ronin_cli.main import app

_runner = CliRunner()


def test_version_flag_prints_and_exits() -> None:
    res = _runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert display_version(__version__) in res.stdout
    assert "ronin" in res.stdout


def test_version_subcommand_still_works() -> None:
    res = _runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert display_version(__version__) in res.stdout


def test_version_flag_short_circuits_before_subcommand() -> None:
    # --version is eager: it prints and exits even with a subcommand present
    res = _runner.invoke(app, ["--version", "doctor"])
    assert res.exit_code == 0
    assert display_version(__version__) in res.stdout
