"""Consent: default off, disclosed once, refusal sticky, corruption fails closed.

Every test here writes inside ``tmp_path``. Nothing may touch a real home directory —
:func:`ronin.telemetry.for_home` takes ``home`` as a parameter for exactly that reason,
and a test that called ``Path.home()`` would edit the developer's own consent record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from telemetry_harness import RecordingSender

from ronin.telemetry import (
    CONSENT_RELATIVE_PATH,
    CONSENT_SCHEMA,
    DISCLOSURE,
    SENT_LOG_RELATIVE_PATH,
    Consent,
    ConsentRecord,
    ConsentStore,
    Telemetry,
    default_consent_path,
    default_log_path,
    for_home,
)


def store(tmp_path: Path) -> ConsentStore:
    return ConsentStore(default_consent_path(tmp_path))


# --------------------------------------------------------------------------- #
# default off
# --------------------------------------------------------------------------- #


def test_the_default_state_is_unasked_and_unasked_never_sends(tmp_path: Path) -> None:
    record = store(tmp_path).read()
    assert record.state is Consent.UNASKED
    assert record.disclosed is False
    assert record.may_send is False


def test_reading_a_missing_record_writes_nothing(tmp_path: Path) -> None:
    """A read must not materialize ``~/.ronin`` — a first run that silently creates a
    directory in someone's home is a first run they do not trust."""
    store(tmp_path).read()
    assert not (tmp_path / ".ronin").exists()


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (Consent.UNASKED, False),
        (Consent.REFUSED, False),
        (Consent.GRANTED, True),
    ],
)
def test_only_granted_may_send(state: Consent, expected: bool) -> None:
    assert ConsentRecord(state=state).may_send is expected


# --------------------------------------------------------------------------- #
# the disclosure, exactly once
# --------------------------------------------------------------------------- #


def test_the_disclosure_is_shown_once_and_never_again(tmp_path: Path) -> None:
    telemetry = for_home(tmp_path)
    assert telemetry.disclosure_once() == DISCLOSURE
    assert telemetry.disclosure_once() is None
    assert telemetry.disclosure_once() is None


def test_the_disclosure_survives_a_restart_because_it_is_recorded(tmp_path: Path) -> None:
    assert for_home(tmp_path).disclosure_once() == DISCLOSURE
    # A fresh Telemetry over the same home is a new process, as far as this is concerned.
    assert for_home(tmp_path).disclosure_once() is None


def test_showing_the_disclosure_is_not_consent(tmp_path: Path) -> None:
    telemetry = for_home(tmp_path)
    telemetry.disclosure_once()
    record = telemetry.state()
    assert record.disclosed is True
    assert record.state is Consent.UNASKED
    assert record.may_send is False


def test_the_disclosure_is_one_line_and_says_the_four_things_it_must(tmp_path: Path) -> None:
    """It is the only privacy notice most users will read, so its content is asserted.

    A notice that does not say how to turn the thing on, how to see what was sent, and
    how to turn it off is a notice that leaves the user with nothing to do."""
    assert "\n" not in DISCLOSURE
    lowered = DISCLOSURE.lower()
    assert "off" in lowered
    assert "nothing has been sent" in lowered
    assert "telemetry on" in lowered
    assert "telemetry show" in lowered
    assert "telemetry off" in lowered
    for forbidden in ("prompts", "code", "paths"):
        assert forbidden in lowered


# --------------------------------------------------------------------------- #
# refusal is sticky
# --------------------------------------------------------------------------- #


def test_refusal_is_sticky_and_never_re_prompts(tmp_path: Path) -> None:
    telemetry = for_home(tmp_path)
    telemetry.consent.refuse()
    assert telemetry.state().state is Consent.REFUSED
    # No disclosure, this process or any later one.
    assert telemetry.disclosure_once() is None
    assert for_home(tmp_path).disclosure_once() is None
    assert for_home(tmp_path).state().state is Consent.REFUSED


async def test_refusal_after_a_grant_stops_sending_immediately(tmp_path: Path) -> None:
    sender = RecordingSender()
    telemetry = for_home(tmp_path, sender=sender)
    telemetry.consent.grant()
    from telemetry_harness import payload

    await telemetry.record(payload())
    assert len(sender.calls) == 1
    telemetry.consent.refuse()
    await telemetry.record(payload())
    assert len(sender.calls) == 1


def test_granting_marks_the_disclosure_shown_because_an_explicit_yes_has_seen_it(
    tmp_path: Path,
) -> None:
    telemetry = for_home(tmp_path)
    telemetry.consent.grant()
    assert telemetry.disclosure_once() is None


# --------------------------------------------------------------------------- #
# the file on disk
# --------------------------------------------------------------------------- #


def test_the_record_lives_under_home_not_the_project(tmp_path: Path) -> None:
    """A refusal that only sticks in one checkout is not a refusal."""
    assert default_consent_path(tmp_path) == tmp_path / CONSENT_RELATIVE_PATH
    assert default_log_path(tmp_path) == tmp_path / SENT_LOG_RELATIVE_PATH


def test_the_written_record_is_sorted_json_with_a_trailing_newline(tmp_path: Path) -> None:
    consent = store(tmp_path)
    consent.grant()
    raw = consent.path.read_bytes()
    assert raw.endswith(b"\n")
    # Bytes, not read_text: universal-newline translation would hide a CRLF regression.
    assert b"\r\n" not in raw
    assert json.loads(raw) == {
        "disclosed": True,
        "schema": CONSENT_SCHEMA,
        "state": "granted",
    }


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("not json at all", "not valid JSON"),
        ("[]", "must contain a JSON object"),
        ('{"state": "granted"}', "schema"),
        ('{"schema": 999, "state": "granted"}', "schema"),
        ('{"schema": 1, "state": "maybe"}', "unknown state"),
        ('{"schema": 1}', "unknown state"),
    ],
)
def test_a_corrupt_record_fails_closed_in_both_directions(
    tmp_path: Path, body: str, reason: str
) -> None:
    """It can neither cause a send nor cause a re-prompt.

    A user who had opted in silently stops being recorded, which is the direction it is
    safe to fail in. The alternative — treating corruption as ``UNASKED`` — would
    re-prompt someone who already refused.
    """
    consent = store(tmp_path)
    consent.path.parent.mkdir(parents=True, exist_ok=True)
    consent.path.write_text(body, encoding="utf-8")
    record = consent.read()
    assert record.state is Consent.REFUSED
    assert record.may_send is False
    assert record.disclosed is True
    assert reason in record.problem


async def test_a_corrupt_record_sends_nothing(tmp_path: Path) -> None:
    from telemetry_harness import payload

    sender = RecordingSender()
    telemetry = for_home(tmp_path, sender=sender)
    telemetry.consent.path.parent.mkdir(parents=True, exist_ok=True)
    telemetry.consent.path.write_text("{", encoding="utf-8")
    await telemetry.record(payload())
    assert sender.never_called


def test_a_corrupt_record_shows_no_disclosure(tmp_path: Path) -> None:
    consent = store(tmp_path)
    consent.path.parent.mkdir(parents=True, exist_ok=True)
    consent.path.write_text("{", encoding="utf-8")
    assert Telemetry(consent=consent).disclosure_once() is None


def test_an_unwritable_location_is_a_value_not_an_exception(tmp_path: Path) -> None:
    """A failure to persist must not raise out of a module the session calls on its way
    out. The cost is named: the answer is not remembered next run."""
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    consent = ConsentStore(blocker / ".ronin" / "telemetry.json")
    written = consent.grant()
    assert written.state is Consent.GRANTED
    assert "could not be written" in written.problem


def test_an_unreadable_record_fails_closed(tmp_path: Path) -> None:
    """A directory where a file should be is the portable way to make a read fail."""
    consent = store(tmp_path)
    consent.path.mkdir(parents=True, exist_ok=True)
    record = consent.read()
    assert record.state is Consent.REFUSED
    assert record.disclosed is True
    assert "could not be read" in record.problem


def test_mark_disclosed_keeps_the_state_it_found(tmp_path: Path) -> None:
    consent = store(tmp_path)
    consent.grant()
    consent.mark_disclosed()
    assert consent.read().state is Consent.GRANTED
