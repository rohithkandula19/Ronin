"""Tests for the ``ronin faithfulness check`` CLI command.

Offline: no provider, no keys, no network. Exercises the real harness through
the CLI surface (argument + piped stdin, --sources, --json, --strict).
"""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ronin_cli.faithfulness_cmd import check, exit_code, load_sources, report_to_dict
from ronin_cli.faithfulness_hook import MODE_WARN
from ronin_cli.main import app

runner = CliRunner()

AUTH_BODY = (
    "def login(username, password):\n"
    "    token = make_token(username)\n"
    "    return Session(token=token)\n"
)


def _write_source(tmp_path: Path) -> Path:
    p = tmp_path / "auth.py"
    p.write_text(AUTH_BODY, encoding="utf-8")
    return p


# ---------- load_sources ----------

def test_load_sources_reads_and_reports_missing(tmp_path: Path) -> None:
    good = _write_source(tmp_path)
    loaded = load_sources([good, tmp_path / "nope.py"])
    assert len(loaded.sources) == 1
    assert loaded.sources[0].origin == str(good)
    assert loaded.missing == [str(tmp_path / "nope.py")]


# ---------- check() core ----------

def test_check_grounded(tmp_path: Path) -> None:
    src = _write_source(tmp_path)
    outcome, missing = check("login calls make_token and returns a Session.", [src], mode=MODE_WARN)
    assert not outcome.ungrounded
    assert missing == []
    assert exit_code(outcome) == 0


def test_check_ungrounded_flags_symbol(tmp_path: Path) -> None:
    src = _write_source(tmp_path)
    outcome, _ = check("login calls refresh_oauth_token to rotate the credentials.", [src])
    assert outcome.ungrounded
    assert "refresh_oauth_token" in outcome.report.hallucinated_symbols
    assert exit_code(outcome) == 1


def test_check_abstains_without_sources() -> None:
    outcome, _ = check("login returns a Session.", [])
    assert outcome.abstain
    assert exit_code(outcome) == 2


def test_report_to_dict_is_json_serializable(tmp_path: Path) -> None:
    src = _write_source(tmp_path)
    outcome, _ = check("login calls make_token.", [src])
    d = report_to_dict(outcome.report)
    assert json.loads(json.dumps(d))  # round-trips
    assert "score" in d and "claims" in d


# ---------- CLI surface ----------

def test_cli_grounded_renders_panel(tmp_path: Path) -> None:
    src = _write_source(tmp_path)
    res = runner.invoke(
        app,
        ["faithfulness", "check", "login calls make_token and returns a Session.",
         "--sources", str(src)],
    )
    assert res.exit_code == 0
    assert "grounded" in res.stdout


def test_cli_ungrounded_with_strict_exits_nonzero(tmp_path: Path) -> None:
    src = _write_source(tmp_path)
    res = runner.invoke(
        app,
        ["faithfulness", "check", "login calls refresh_oauth_token.",
         "--sources", str(src), "--strict"],
    )
    assert res.exit_code == 1
    assert "ungrounded" in res.stdout


def test_cli_json_output(tmp_path: Path) -> None:
    src = _write_source(tmp_path)
    res = runner.invoke(
        app,
        ["faithfulness", "check", "PaymentGateway.charge validates the credit_card_number.",
         "--sources", str(src), "--json"],
    )
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["ungrounded"] is True
    assert "PaymentGateway.charge" in payload["hallucinated_symbols"]


def test_cli_strict_abstain_exits_two(tmp_path: Path) -> None:
    # No sources -> abstain -> strict exit code 2.
    res = runner.invoke(
        app, ["faithfulness", "check", "login returns a Session.", "--strict"]
    )
    assert res.exit_code == 2


def test_cli_empty_input_errors() -> None:
    res = runner.invoke(app, ["faithfulness", "check", ""])
    assert res.exit_code == 2
    assert "nothing to check" in res.stdout


def test_cli_reports_missing_source(tmp_path: Path) -> None:
    res = runner.invoke(
        app,
        ["faithfulness", "check", "login calls make_token.",
         "--sources", str(tmp_path / "ghost.py")],
    )
    # Missing source -> no evidence -> abstain, plus a warning line.
    assert "could not read source" in res.stdout


# ---------- config set faithfulness=mode ----------

def test_config_set_faithfulness_mode() -> None:
    from ronin_cli.config_cmd import BadSetting, coerce_value

    assert coerce_value("faithfulness", "warn") == "warn"
    assert coerce_value("faithfulness", "GATE") == "gate"
    assert coerce_value("faithfulness", "off") == "off"
    try:
        coerce_value("faithfulness", "loud")
    except BadSetting as e:
        assert "off, warn, gate" in str(e)
    else:  # pragma: no cover
        raise AssertionError("bad mode should have raised")


def test_config_set_persists_faithfulness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["config", "--set", "faithfulness=gate"])
    assert res.exit_code == 0
    from ronin_cli.config import load_config
    assert load_config().faithfulness == "gate"
