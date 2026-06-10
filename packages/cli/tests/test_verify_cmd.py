"""Tests for polyglot verify-command detection + parsing."""
from __future__ import annotations

import json
from pathlib import Path

from ronin_cli.verify_cmd import detect_verify_command, parse_verdict, run_verify


def test_memory_verify_key_wins(tmp_path: Path) -> None:
    (tmp_path / "RONIN.md").write_text("# notes\nverify: make check\n", encoding="utf-8")
    # even with a package.json present, the explicit verify: line wins
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    cmd, runner = detect_verify_command(tmp_path)
    assert cmd == ["make", "check"] and runner == "memory"


def test_node_test_picks_package_manager(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert detect_verify_command(tmp_path) == (["npm", "test"], "node")
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert detect_verify_command(tmp_path) == (["pnpm", "test"], "node")


def test_node_without_test_script_skipped(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "tsc"}}))
    # no test script → fall through (nothing else here) → None
    assert detect_verify_command(tmp_path) is None


def test_cargo_and_go(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'")
    assert detect_verify_command(tmp_path) == (["cargo", "test"], "cargo")
    (tmp_path / "Cargo.toml").unlink()
    (tmp_path / "go.mod").write_text("module x")
    assert detect_verify_command(tmp_path) == (["go", "test", "./..."], "go")


def test_makefile_test_target(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("build:\n\tgcc x\ntest:\n\t./run\n")
    assert detect_verify_command(tmp_path) == (["make", "test"], "make")
    # a Makefile without a test target is not a match
    (tmp_path / "Makefile").write_text("build:\n\tgcc x\n")
    assert detect_verify_command(tmp_path) is None


def test_python_markers(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    cmd, runner = detect_verify_command(tmp_path)
    assert runner == "pytest" and cmd[-2:] == ["pytest", "-q"]


def test_no_command_detected(tmp_path: Path) -> None:
    assert detect_verify_command(tmp_path) is None


def test_priority_node_before_python(tmp_path: Path) -> None:
    # a polyglot repo: package.json test script beats pytest markers
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (tmp_path / "tests").mkdir()
    _, runner = detect_verify_command(tmp_path)
    assert runner == "node"


# ---- parsing ----

def test_parse_pytest() -> None:
    ok, summ = parse_verdict("pytest", "..\n3 passed in 0.1s", 0)
    assert ok is True
    ok, _ = parse_verdict("pytest", "1 failed, 2 passed in 0.1s", 1)
    assert ok is False


def test_parse_cargo() -> None:
    ok, summ = parse_verdict("cargo", "test result: ok. 5 passed; 0 failed", 0)
    assert ok is True and "ok" in summ
    ok, _ = parse_verdict("cargo", "test result: FAILED. 1 failed", 101)
    assert ok is False


def test_parse_go() -> None:
    assert parse_verdict("go", "ok  \tmypkg\t0.2s", 0)[0] is True
    assert parse_verdict("go", "--- FAIL: TestX\nFAIL", 1)[0] is False


def test_parse_generic_trusts_exit_code() -> None:
    assert parse_verdict("node", "Tests: 3 passed", 0)[0] is True
    assert parse_verdict("node", "Tests: 1 failed", 1)[0] is False


def test_run_verify_no_command_is_not_a_failure(tmp_path: Path) -> None:
    v = run_verify(tmp_path)
    assert v.ran is False and v.passed is False and v.runner == "none"


def test_run_verify_runs_real_make(tmp_path: Path) -> None:
    # a tiny real Makefile that passes — exercises detection + run + parse
    (tmp_path / "Makefile").write_text("test:\n\t@echo all good\n")
    v = run_verify(tmp_path)
    if v.command:  # make may be absent on minimal CI images
        assert v.ran is True
        assert v.passed is True
        assert "good" in v.output


# ---- CLI: `ronin verify` ----

from typer.testing import CliRunner  # noqa: E402

from ronin_cli.main import app  # noqa: E402

_runner = CliRunner()


def test_cli_verify_no_command(tmp_path: Path) -> None:
    res = _runner.invoke(app, ["verify", "--root", str(tmp_path)])
    assert res.exit_code == 2
    assert "no test command detected" in res.stdout


def test_cli_verify_green(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\t@echo ok\n")
    res = _runner.invoke(app, ["verify", "--root", str(tmp_path)])
    # make may be unavailable on a minimal image; only assert when it ran
    if "make" in res.stdout:
        assert res.exit_code == 0
        assert "green" in res.stdout


def test_cli_verify_red_exit_code(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\t@exit 1\n")
    res = _runner.invoke(app, ["verify", "--root", str(tmp_path)])
    if "make" in res.stdout:
        assert res.exit_code == 1
        assert "red" in res.stdout


# ---- review fixes: placeholder skip, shlex, parser correctness ----

def test_echo_placeholder_node_script_is_skipped(tmp_path: Path) -> None:
    # ronin's own root had `"test": "echo 'no tests yet'"` — must NOT shadow pytest
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "echo 'no tests yet'"}}))
    (tmp_path / "tests").mkdir()
    cmd, runner = detect_verify_command(tmp_path)
    assert runner == "pytest"          # the echo placeholder was skipped


def test_npm_init_placeholder_skipped(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
    assert detect_verify_command(tmp_path) is None   # no real suite here


def test_verify_line_honors_quotes_and_comments(tmp_path: Path) -> None:
    (tmp_path / "RONIN.md").write_text(
        'verify: pytest -k "not slow"   # nightly\n', encoding="utf-8")
    cmd, runner = detect_verify_command(tmp_path)
    assert runner == "memory"
    assert cmd == ["pytest", "-k", "not slow"]      # quotes joined, comment stripped


def test_verify_line_markdown_bullet(tmp_path: Path) -> None:
    (tmp_path / "RONIN.md").write_text("- verify: make test\n", encoding="utf-8")
    assert detect_verify_command(tmp_path) == (["make", "test"], "memory")


def test_cargo_summary_prefers_failed_line() -> None:
    out = "test result: ok. 5 passed; 0 failed\ntest result: FAILED. 1 passed; 1 failed"
    ok, summary = parse_verdict("cargo", out, 101)
    assert ok is False                  # returncode authoritative
    assert "FAILED" in summary          # not the earlier 'ok' line


def test_go_passing_with_FAIL_substring_not_false_failed() -> None:
    # an uppercase FAIL in a package path must not flip a green (exit 0) run
    out = "ok  \tgithub.com/x/FAILSAFE\t0.1s"
    ok, _ = parse_verdict("go", out, 0)
    assert ok is True
