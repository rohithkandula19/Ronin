"""``ronin telemetry``: the opt-in surface, and the properties that make it trustworthy.

:mod:`ronin.telemetry` had consent storage, bucketing, a payload log and a disclosure
string, with no way for a user to reach any of it. A privacy control that exists only as
a Python API is not a control.

The tests worth having here are not "does the verb run". They are the four claims the
feature makes about itself:

* **off is the default**, and stays off through a fresh run;
* **the noun alone does not opt you in** — bare ``telemetry`` shows status;
* **the disclosure appears once, on stderr**, and never blocks;
* **``show`` prints the log verbatim**, because auditing rather than trusting is the
  entire point of keeping a local copy.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ronin.cli.main import Command, Options, Streams, Usage, parse
from ronin.cli.telemetry_cmd import (
    FIELD_GLOSS,
    TelemetryOptions,
    Verb,
    render_fields,
    run,
)
from ronin.telemetry import (
    PAYLOAD_FIELDS,
    Consent,
    ConsentStore,
    default_consent_path,
    default_log_path,
)


def options(argv: list[str]) -> Options:
    parsed = parse(argv)
    assert not isinstance(parsed, Usage), parsed
    return parsed


def at(home: Path, verb: Verb, *, limit: int = 0) -> tuple[int, str, str]:
    return run(TelemetryOptions(verb=verb, home=home, limit=limit))


# --------------------------------------------------------------------------- #
# argv
# --------------------------------------------------------------------------- #


def test_the_bare_noun_shows_status_and_does_not_opt_in() -> None:
    """A verb that enables collection when the user typed only the noun is a dark
    pattern. ``ronin telemetry`` must be safe to type out of curiosity."""
    parsed = options(["telemetry"])
    assert parsed.command is Command.TELEMETRY
    assert parsed.telemetry is not None
    assert parsed.telemetry.verb is Verb.STATUS


@pytest.mark.parametrize("verb", list(Verb), ids=[v.value for v in Verb])
def test_every_verb_parses(verb: Verb) -> None:
    parsed = options(["telemetry", verb.value])
    assert parsed.telemetry is not None
    assert parsed.telemetry.verb is verb


def test_an_unknown_verb_is_a_usage_error_that_lists_the_real_ones() -> None:
    usage = parse(["telemetry", "enable"])
    assert isinstance(usage, Usage)
    assert "unknown telemetry verb" in usage.message
    for verb in Verb:
        assert verb.value in usage.message


def test_no_other_command_carries_telemetry_options() -> None:
    assert options(["doctor"]).telemetry is None
    assert options(["hello there"]).telemetry is None


# --------------------------------------------------------------------------- #
# The disclosure
# --------------------------------------------------------------------------- #


def test_the_field_list_is_derived_from_the_real_payload_schema() -> None:
    """A privacy notice listing yesterday's fields is evidence that nobody checks."""
    rendered = render_fields()
    for name in PAYLOAD_FIELDS:
        assert name in rendered


def test_a_payload_field_with_no_description_is_a_hard_error() -> None:
    """Adding something to the payload must force a decision about how to explain it.

    Printing a bare field name would satisfy a "the notice lists every field" check
    while telling the reader nothing, which is the failure mode this guards.
    """
    assert set(PAYLOAD_FIELDS) <= set(FIELD_GLOSS), (
        "every telemetry payload field needs an entry in FIELD_GLOSS"
    )


def test_turning_it_on_states_what_is_never_sent(tmp_path: Path) -> None:
    """The negative list is the falsifiable half.

    "aggregate task outcomes" is reassuring and unfalsifiable; "never your prompts,
    code, paths or commands" is a claim a reader can check against `show`.
    """
    code, out, _ = at(tmp_path, Verb.ON)
    assert code == 0
    assert "what is never sent" in out
    for forbidden in ("prompts", "code", "paths", "commands", "API keys"):
        assert forbidden in out
    assert "bucketed, not exact" in out


def test_granting_does_not_claim_transmission_that_is_not_happening(
    tmp_path: Path,
) -> None:
    """No sender is wired in this build, so "outcomes are being sent" would be false.

    A privacy surface that overstates what it does is not a safe error in either
    direction: it makes a cautious user opt out of nothing, and it trains a trusting one
    to accept the claim next time, when it might be live.
    """
    from ronin.cli.telemetry_cmd import NO_ENDPOINT, sender_configured

    assert sender_configured(tmp_path) is False, "this test describes the unwired build"

    _, on_out, _ = at(tmp_path, Verb.ON)
    assert NO_ENDPOINT in on_out

    _, status_out, _ = at(tmp_path, Verb.STATUS)
    assert "consent is granted" in status_out
    assert "nothing is transmitted" in status_out
    assert "are being sent" not in status_out


def test_the_field_list_is_framed_as_what_would_be_sent(tmp_path: Path) -> None:
    """Present tense would assert transmission the build does not perform."""
    _, out, _ = at(tmp_path, Verb.ON)
    assert "what would be sent" in out


def test_the_note_disappears_on_its_own_once_a_sender_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Derived from ``for_home``, not hardcoded, so wiring an endpoint updates the text.

    Without that derivation the note becomes a stale disclaimer claiming nothing is sent
    while something is — strictly worse than the overstatement it was written to avoid.
    """
    import ronin.cli.telemetry_cmd as module
    from ronin.telemetry import Telemetry
    from ronin.telemetry import for_home as real_for_home

    async def send(payload: Mapping[str, str | bool]) -> None:
        raise AssertionError("nothing should actually be sent by this test")

    def wired(home: Path) -> Telemetry:
        base = real_for_home(home)
        return Telemetry(consent=base.consent, sender=send, log=base.log)

    assert module.sender_configured(tmp_path) is False
    monkeypatch.setattr(module, "for_home", wired)
    assert module.sender_configured(tmp_path) is True

    _, out, _ = at(tmp_path, Verb.ON)
    assert module.NO_ENDPOINT not in out
    assert "are being sent" in at(tmp_path, Verb.STATUS)[1]


# --------------------------------------------------------------------------- #
# State transitions
# --------------------------------------------------------------------------- #


def test_off_is_the_state_before_anyone_is_asked(tmp_path: Path) -> None:
    code, out, _ = at(tmp_path, Verb.STATUS)
    assert code == 0
    assert "consent: unasked" in out
    assert "telemetry is OFF" in out
    assert not default_consent_path(tmp_path).exists(), "status must not create state"


def test_on_then_off_round_trips_on_disk(tmp_path: Path) -> None:
    at(tmp_path, Verb.ON)
    store = ConsentStore(default_consent_path(tmp_path))
    assert store.read().state is Consent.GRANTED
    assert "granted" in at(tmp_path, Verb.STATUS)[1]

    at(tmp_path, Verb.OFF)
    assert store.read().state is Consent.REFUSED
    assert "nothing is being sent" in at(tmp_path, Verb.STATUS)[1]


def test_off_says_the_local_log_is_kept_and_whose_job_deleting_it_is(
    tmp_path: Path,
) -> None:
    """Silently deleting a user's audit trail on opt-out would remove the evidence
    they might most want. Silently keeping it without saying so is worse."""
    _, out, _ = at(tmp_path, Verb.OFF)
    assert "delete it yourself" in out
    assert str(default_log_path(tmp_path)) in out


# --------------------------------------------------------------------------- #
# show
# --------------------------------------------------------------------------- #


def test_show_on_a_machine_that_never_sent_anything_says_so(tmp_path: Path) -> None:
    code, out, _ = at(tmp_path, Verb.SHOW)
    assert code == 0
    assert "no telemetry has been sent" in out


def test_show_prints_the_log_verbatim(tmp_path: Path) -> None:
    """Byte-for-byte. Reformatting would add a layer that could disagree with what
    was actually sent, and that disagreement is exactly what `show` exists to rule
    out."""
    log = default_log_path(tmp_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        json.dumps({"outcome": "solved", "turn_bucket": "few"}),
        json.dumps({"outcome": "failed", "taxonomy_class": "wrong_file"}),
    ]
    log.write_text("\n".join(rows) + "\n", encoding="utf-8")

    code, out, _ = at(tmp_path, Verb.SHOW)
    assert code == 0
    assert "2 payload(s) sent" in out
    for row in rows:
        assert row in out, "the exact wire form must appear unmodified"


def test_show_can_be_limited_to_the_most_recent(tmp_path: Path) -> None:
    log = default_log_path(tmp_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "\n".join(json.dumps({"n": index}) for index in range(5)) + "\n", encoding="utf-8"
    )
    _, out, _ = at(tmp_path, Verb.SHOW, limit=2)
    assert "showing the last 2" in out
    assert json.dumps({"n": 4}) in out
    assert json.dumps({"n": 0}) not in out


def test_show_reports_the_total_even_when_truncating(tmp_path: Path) -> None:
    """"showing 2" without "of 5" invites the reader to think 2 is all there is."""
    log = default_log_path(tmp_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "\n".join(json.dumps({"n": index}) for index in range(5)) + "\n", encoding="utf-8"
    )
    _, out, _ = at(tmp_path, Verb.SHOW, limit=2)
    assert "5 payload(s) sent" in out


# --------------------------------------------------------------------------- #
# Failing readably
# --------------------------------------------------------------------------- #


def test_a_corrupt_consent_file_reports_the_problem_in_status(tmp_path: Path) -> None:
    """It fails closed to "nothing is sent", and says the choice was not read.

    Without the note a user who granted consent and then hit a corrupt file would see
    "off" and conclude they had chosen it.
    """
    path = default_consent_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    code, out, _ = at(tmp_path, Verb.STATUS)
    assert code == 0
    assert "note:" in out


def test_an_unreadable_log_is_an_error_rather_than_an_empty_report(
    tmp_path: Path,
) -> None:
    """Reporting "no telemetry sent" for a log that exists but cannot be read would be
    a false all-clear about the one thing this command is for."""
    log = default_log_path(tmp_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.mkdir()  # a directory where a file belongs: readable path, unreadable content
    code, _, err = at(tmp_path, Verb.SHOW)
    assert code == 1
    assert "cannot read" in err


# --------------------------------------------------------------------------- #
# The doctor line
# --------------------------------------------------------------------------- #


async def test_doctor_reports_telemetry_and_never_nags(tmp_path: Path) -> None:
    """Off is correct, so the check is OK in every honest state.

    A diagnostic that warned about telemetry being off would be pressure dressed as
    advice, and `doctor`'s exit code is consumed by scripts.
    """
    from ronin.cli.doctor import CheckStatus, run_doctor
    from ronin.cli.spine import Paths
    from ronin.cli.wire import load_workspace

    (tmp_path / ".git").mkdir()
    paths = Paths.discover(tmp_path, home=tmp_path)
    report = await run_doctor(load_workspace(paths, environ={}), environ={})
    check = report.check("telemetry")
    assert check is not None
    assert check.status is CheckStatus.OK
    assert "off" in check.detail

    at(tmp_path, Verb.ON)
    granted = await run_doctor(load_workspace(paths, environ={}), environ={})
    on_check = granted.check("telemetry")
    assert on_check is not None
    assert on_check.status is CheckStatus.OK
    assert "on" in on_check.detail


async def test_doctor_warns_only_when_the_recorded_choice_cannot_be_read(
    tmp_path: Path,
) -> None:
    from ronin.cli.doctor import CheckStatus, run_doctor
    from ronin.cli.spine import Paths
    from ronin.cli.wire import load_workspace

    (tmp_path / ".git").mkdir()
    path = default_consent_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage", encoding="utf-8")

    paths = Paths.discover(tmp_path, home=tmp_path)
    report = await run_doctor(load_workspace(paths, environ={}), environ={})
    check = report.check("telemetry")
    assert check is not None
    assert check.status is CheckStatus.WARN
    assert check.remedy, "a warning with no remedy is a log line"
    # A warning must not make `doctor` exit non-zero — degradation is not breakage.
    assert report.exit_code() == 0


# --------------------------------------------------------------------------- #
# The first-run disclosure
# --------------------------------------------------------------------------- #


def collecting() -> tuple[Streams, list[str], list[str]]:
    out: list[str] = []
    err: list[str] = []
    return (
        Streams(out=out.append, err=err.append, ask=lambda _: "", flush=lambda: None),
        out,
        err,
    )


def test_the_disclosure_goes_to_stderr_once_and_marks_itself(tmp_path: Path) -> None:
    """stdout is the agent's answer and may be piped into a parser; a privacy notice
    inside a JSON payload would be both useless and a bug."""
    from ronin.cli.main import _disclose_telemetry_once
    from ronin.cli.spine import Paths

    paths = Paths.discover(tmp_path, home=tmp_path)
    streams, out, err = collecting()

    _disclose_telemetry_once(paths, streams)
    assert out == [], "nothing on stdout"
    assert len(err) == 1
    assert "telemetry is OFF" in err[0]

    _disclose_telemetry_once(paths, streams)
    assert len(err) == 1, "shown once, not every run"


def test_the_disclosure_does_not_enable_anything(tmp_path: Path) -> None:
    """It is a notice, not a prompt: consent stays UNASKED until someone opts in."""
    from ronin.cli.main import _disclose_telemetry_once
    from ronin.cli.spine import Paths

    paths = Paths.discover(tmp_path, home=tmp_path)
    streams, _, _ = collecting()
    _disclose_telemetry_once(paths, streams)
    assert ConsentStore(default_consent_path(tmp_path)).read().state is Consent.UNASKED


def test_a_user_who_already_chose_is_not_shown_it_again(tmp_path: Path) -> None:
    from ronin.cli.main import _disclose_telemetry_once
    from ronin.cli.spine import Paths

    at(tmp_path, Verb.OFF)
    paths = Paths.discover(tmp_path, home=tmp_path)
    streams, _, err = collecting()
    _disclose_telemetry_once(paths, streams)
    assert err == []
