"""`ronin eval run` must not use hardcoded placeholder models and must SKIP
honestly (no fake score) when there is no usable auth (Stage A blocker 0.5)."""
from __future__ import annotations

from typer.testing import CliRunner

from ronin_cli.main import app

runner = CliRunner()


class _StubConfig:
    def __init__(self, provider="cerebras", model="gpt-oss-120b", authed=False):
        self.provider = provider
        self._model = model
        self._authed = authed

    def resolved_model(self):
        return self._model

    def has_provider_auth(self):
        return self._authed


def _dataset(tmp_path):
    ds = tmp_path / "d.jsonl"
    ds.write_text('{"input": "x", "expected": "y"}\n', encoding="utf-8")
    return ds


def _spy(captured):
    """A stand-in for ronin_eval_suite.cli.main that records argv and returns 0."""
    def _f(argv):
        captured["argv"] = argv
        return 0
    return _f


def test_eval_run_skips_with_reason_when_no_auth(monkeypatch, tmp_path):
    monkeypatch.setattr("ronin_cli.main.load_config", lambda: _StubConfig(authed=False))
    called = {}
    monkeypatch.setattr("ronin_eval_suite.cli.main", _spy(called))
    r = runner.invoke(app, ["eval", "run", str(_dataset(tmp_path))])
    assert r.exit_code == 3, r.output          # 3 == SKIPPED (not 0 pass / not a fake fail)
    assert "SKIPPED" in r.output
    assert "argv" not in called                # never reached the suite runner -> no fake score


def test_eval_run_uses_configured_model_not_placeholder(monkeypatch, tmp_path):
    monkeypatch.setattr("ronin_cli.main.load_config",
                        lambda: _StubConfig(model="gpt-oss-120b", authed=True))
    captured = {}
    monkeypatch.setattr("ronin_eval_suite.cli.main", _spy(captured))
    r = runner.invoke(app, ["eval", "run", str(_dataset(tmp_path))])
    assert r.exit_code == 0, r.output
    argv = captured["argv"]
    assert "claude-sonnet-4-6" not in argv     # no hardcoded Anthropic placeholder
    assert "claude-opus-4-7" not in argv
    assert argv[argv.index("--target") + 1] == "gpt-oss-120b"
    assert argv[argv.index("--judge") + 1] == "gpt-oss-120b"


def test_eval_run_explicit_target_and_judge_bypass_auth_skip(monkeypatch, tmp_path):
    # Naming both target and judge explicitly means the user has access; don't
    # skip on the configured provider's auth.
    monkeypatch.setattr("ronin_cli.main.load_config", lambda: _StubConfig(authed=False))
    captured = {}
    monkeypatch.setattr("ronin_eval_suite.cli.main", _spy(captured))
    r = runner.invoke(app, ["eval", "run", str(_dataset(tmp_path)),
                            "--target", "m1", "--judge", "m2"])
    assert r.exit_code == 0, r.output
    assert captured["argv"][captured["argv"].index("--target") + 1] == "m1"
