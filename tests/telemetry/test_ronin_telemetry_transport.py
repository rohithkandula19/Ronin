"""Transport: nothing is sent without consent, nothing that fails breaks a turn.

Two assertions in here are the reason the module is shaped the way it is:

* :func:`test_the_sender_is_never_called_without_consent` — the sender is not merely
  told "no", it is never invoked, so a sender with a side effect at call time cannot
  leak anything.
* :func:`test_the_module_imports_no_http_client` — asserted by walking the module's
  AST, because a lazy ``import httpx`` inside a function is invisible to any runtime
  check and is exactly how a socket gets into a module that promises none. The AST
  rather than a grep: the first version of this test failed on the docstring that
  *mentions* httpx.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from telemetry_harness import REPO_ROOT, RecordingSender, payload, unreachable_sender

from ronin.telemetry import (
    PAYLOAD_FIELDS,
    Consent,
    ConsentStore,
    PayloadLog,
    PayloadValue,
    RecordResult,
    Telemetry,
    default_consent_path,
    for_home,
    serialize,
)

# --------------------------------------------------------------------------- #
# consent gates the sender, not the other way round
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state", [Consent.UNASKED, Consent.REFUSED])
async def test_the_sender_is_never_called_without_consent(tmp_path: Path, state: Consent) -> None:
    sender = unreachable_sender()
    telemetry = for_home(tmp_path, sender=sender)
    if state is Consent.REFUSED:
        telemetry.consent.refuse()
    result = await telemetry.record(payload())
    assert result is RecordResult.NO_CONSENT
    assert sender.never_called


async def test_nothing_is_logged_without_consent(tmp_path: Path) -> None:
    """The audit log is a record of what was sent. A payload that was never sent must
    not appear in it, or 'nothing has been sent' stops being checkable."""
    telemetry = for_home(tmp_path, sender=unreachable_sender())
    await telemetry.record(payload())
    assert telemetry.log is not None
    assert telemetry.log.lines() == ()
    assert not telemetry.log.path.exists()


async def test_consent_is_read_fresh_on_every_record(tmp_path: Path) -> None:
    """A record cached at construction would keep sending after `telemetry off`."""
    sender = RecordingSender()
    telemetry = for_home(tmp_path, sender=sender)
    telemetry.consent.grant()
    await telemetry.record(payload())
    # Another process — or the user — revokes it.
    ConsentStore(default_consent_path(tmp_path)).refuse()
    assert await telemetry.record(payload()) is RecordResult.NO_CONSENT
    assert len(sender.calls) == 1


async def test_consent_without_a_sender_is_not_an_error(tmp_path: Path) -> None:
    """The common wiring: a build with no endpoint configured. Reporting this as a
    failure would put a scary line in front of a user who did nothing wrong."""
    telemetry = for_home(tmp_path)
    telemetry.consent.grant()
    assert await telemetry.record(payload()) is RecordResult.NO_SENDER


# --------------------------------------------------------------------------- #
# what the sender actually receives
# --------------------------------------------------------------------------- #


async def test_the_sender_receives_exactly_the_allowlisted_keys(tmp_path: Path) -> None:
    sender = RecordingSender()
    telemetry = for_home(tmp_path, sender=sender)
    telemetry.consent.grant()
    assert await telemetry.record(payload()) is RecordResult.SENT
    assert tuple(sorted(sender.calls[0])) == PAYLOAD_FIELDS
    assert sender.calls[0] == serialize(payload())


# --------------------------------------------------------------------------- #
# a failing sender must never reach the caller
# --------------------------------------------------------------------------- #


async def test_a_sender_that_raises_does_not_propagate(tmp_path: Path) -> None:
    sender = RecordingSender(fail=True)
    telemetry = for_home(tmp_path, sender=sender)
    telemetry.consent.grant()
    assert await telemetry.record(payload()) is RecordResult.FAILED
    assert len(sender.calls) == 1


async def test_a_sender_that_hangs_is_abandoned_rather_than_stalling_a_turn(
    tmp_path: Path,
) -> None:
    sender = RecordingSender(hang=True)
    telemetry = Telemetry(
        consent=ConsentStore(default_consent_path(tmp_path)),
        sender=sender,
        # A real timeout in a test would make the suite slow; the value is injected for
        # exactly this reason, and the *behaviour* under test is the abandonment.
        timeout=0.01,
    )
    telemetry.consent.grant()
    assert await telemetry.record(payload()) is RecordResult.FAILED


async def test_a_cancelled_turn_is_not_held_up_finishing_a_metric(tmp_path: Path) -> None:
    """``CancelledError`` is a ``BaseException`` and must still propagate: a user who
    pressed ctrl-c is not waiting on telemetry."""

    async def cancelling(fields: Mapping[str, PayloadValue]) -> None:
        raise asyncio.CancelledError

    telemetry = for_home(tmp_path, sender=cancelling)
    telemetry.consent.grant()
    with pytest.raises(asyncio.CancelledError):
        await telemetry.record(payload())


async def test_a_log_that_cannot_be_written_does_not_stop_the_send(tmp_path: Path) -> None:
    sender = RecordingSender()
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where a directory should be", encoding="utf-8")
    telemetry = Telemetry(
        consent=ConsentStore(default_consent_path(tmp_path)),
        sender=sender,
        log=PayloadLog(blocker / "sent.jsonl"),
    )
    telemetry.consent.grant()
    assert await telemetry.record(payload()) is RecordResult.SENT
    assert len(sender.calls) == 1


# --------------------------------------------------------------------------- #
# the audit log
# --------------------------------------------------------------------------- #


async def test_the_log_holds_the_exact_wire_form_one_object_per_line(
    tmp_path: Path,
) -> None:
    sender = RecordingSender()
    telemetry = for_home(tmp_path, sender=sender)
    telemetry.consent.grant()
    await telemetry.record(payload())
    await telemetry.record(payload())
    assert telemetry.log is not None
    lines = telemetry.log.lines()
    assert len(lines) == 2
    for line in lines:
        assert json.loads(line) == serialize(payload())
    raw = telemetry.log.path.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw


async def test_the_log_records_a_send_that_failed(tmp_path: Path) -> None:
    """Written before the attempt, so a crash mid-send still leaves the record. The log
    can over-report what left the machine, never under-report it."""
    telemetry = for_home(tmp_path, sender=RecordingSender(fail=True))
    telemetry.consent.grant()
    assert await telemetry.record(payload()) is RecordResult.FAILED
    assert telemetry.log is not None
    assert len(telemetry.log.lines()) == 1


def test_an_empty_log_says_nothing_has_been_sent(tmp_path: Path) -> None:
    """The sentence a suspicious user needs the tool to be willing to say."""
    rendered = PayloadLog(tmp_path / "sent.jsonl").render()
    assert "nothing has been sent" in rendered
    assert str(tmp_path / "sent.jsonl") in rendered


async def test_the_rendered_log_names_its_own_path_and_counts_the_lines(
    tmp_path: Path,
) -> None:
    telemetry = for_home(tmp_path, sender=RecordingSender())
    telemetry.consent.grant()
    await telemetry.record(payload())
    assert telemetry.log is not None
    rendered = telemetry.log.render()
    assert "1 payload(s) sent" in rendered
    assert str(telemetry.log.path) in rendered
    assert rendered.endswith("\n")


# --------------------------------------------------------------------------- #
# structural guarantees, asserted rather than trusted
# --------------------------------------------------------------------------- #


#: Every module ``ronin.telemetry`` imports, at module scope *or* inside a function.
#: Walked with ``ast`` rather than grepped, because a docstring that *mentions* httpx is
#: not an import of it and a grep cannot tell the difference — the first version of this
#: test failed on its own module docstring.
def _imported_modules() -> set[str]:
    import ast

    source = (REPO_ROOT / "src" / "ronin" / "telemetry.py").read_text(encoding="utf-8")
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_the_module_imports_no_http_client() -> None:
    """The whole point of the injected sender is that this module cannot open a socket.

    An ``import httpx`` anywhere in it — even three levels down inside a helper — would
    put a network dependency in the default install and a mocked network in every test
    that touches a session. Lazy imports are the case that matters, which is why this
    walks the AST instead of checking ``sys.modules``.
    """
    forbidden = {
        "httpx",
        "requests",
        "urllib",
        "urllib.request",
        "socket",
        "http",
        "http.client",
        "aiohttp",
        "ssl",
    }
    assert _imported_modules() & forbidden == set()


def test_the_module_reads_no_environment() -> None:
    """``os.environ`` is the classic hidden input. Every path here is injected."""
    import ast

    source = (REPO_ROOT / "src" / "ronin" / "telemetry.py").read_text(encoding="utf-8")
    assert "os" not in _imported_modules(), "importing os is how an environ read gets in"
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            # No `Path.home()` either: `for_home` takes the directory as a parameter so a
            # test cannot accidentally write to the developer's own consent record.
            assert node.attr not in ("environ", "getenv", "home"), ast.dump(node)
        if isinstance(node, ast.Name):
            assert node.id not in ("getenv", "environ"), node.id


def test_the_module_holds_no_mutable_module_level_state() -> None:
    """A module-level dict or list here would be a cross-session channel."""
    import ronin.telemetry as module

    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (list, dict, set, bytearray)), name


async def test_two_telemetry_objects_over_one_home_agree(tmp_path: Path) -> None:
    """The integration case: the state lives in the file, not in the object, so a CLI
    that constructs one per command behaves like one that constructs one per process."""
    first = for_home(tmp_path, sender=RecordingSender())
    second = for_home(tmp_path, sender=RecordingSender())
    assert first.disclosure_once() is not None
    assert second.disclosure_once() is None
    first.consent.grant()
    assert second.state().may_send is True
    assert await second.record(payload()) is RecordResult.SENT
    assert first.log is not None
    assert len(first.log.lines()) == 1
