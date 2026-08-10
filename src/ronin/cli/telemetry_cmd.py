"""``ronin telemetry on|off|status|show``: the whole opt-in surface, as a command.

:mod:`ronin.telemetry` had consent storage, a payload log, bucketing and a disclosure
string, and no way for a user to reach any of it. A privacy control that exists only as
a Python API is not a control — the person it protects cannot operate it.

Four verbs, and the shape of them is the point:

``status``  what state consent is in and where the files are. Reads nothing else.
``on``      grant. Prints what will and will not be sent, *then* records the grant.
``off``     refuse. Also the state a corrupt or unreadable record resolves to.
``show``    print every payload that was ever sent, from the local append-only log.

``show`` is the load-bearing one. It exists so a user can *audit* rather than trust,
and it prints the exact wire form with no interpretation layer that could disagree
with what actually went out. A telemetry feature whose only evidence is a paragraph in
a README asks for trust it has not earned.

Nothing here sends anything. :class:`~ronin.telemetry.Telemetry` owns that, and it
refuses in every consent state but ``GRANTED``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from ..telemetry import (
    DISCLOSURE,
    PAYLOAD_FIELDS,
    Consent,
    ConsentStore,
    default_consent_path,
    default_log_path,
    for_home,
)

EXIT_OK: Final = 0
EXIT_ERROR: Final = 1

#: Printed by ``on`` before the grant is recorded. Spelling out the *negative* list is
#: the part that matters: "aggregate task outcomes" is reassuring and unfalsifiable,
#: while naming what is excluded is a claim a reader can check against ``show``.
WHAT_IS_SENT = """\
what would be sent, per completed task:
{fields}

what is never sent:
  your prompts, your code, file contents, file names, paths, shell commands,
  repository names, git remotes, branch names, usernames, hostnames, IP addresses,
  API keys, or any identifier that could be traced back to you or your employer.

Turn counts and durations are bucketed, not exact, so a payload cannot be used to
fingerprint one particular session. Every payload is appended to a local log first —
`ronin telemetry show` prints it — so you can audit what left rather than trust this
paragraph.
"""

#: Appended whenever consent is granted but no sender is wired. Derived from the real
#: object rather than hardcoded, so this stops appearing on its own the day an endpoint
#: exists — and until then, saying "outcomes are being sent" would be false.
NO_ENDPOINT = (
    "NOTE: this build has no telemetry endpoint configured, so nothing is transmitted "
    "even with consent granted — `record()` returns no_sender and never opens a socket. "
    "Your choice is stored and will be honoured if an endpoint is ever wired."
)


def sender_configured(home: Path) -> bool:
    """Whether the conventional wiring has somewhere to send to.

    Asked of :func:`~ronin.telemetry.for_home`, which is what a caller would use, so
    this answer changes automatically when a sender is added there instead of a
    hardcoded sentence going stale.
    """
    return for_home(home).sender is not None


class Verb(StrEnum):
    """The four subcommands. A closed set: an unknown verb is a usage error."""

    STATUS = "status"
    ON = "on"
    OFF = "off"
    SHOW = "show"


@dataclass(frozen=True, slots=True)
class TelemetryOptions:
    """Where the state lives and what to do to it. Built by ``main``, which owns argv.

    ``home`` is a parameter rather than ``Path.home()`` so a test describes a machine
    in ``tmp_path`` and nothing writes to a developer's real home directory — the same
    rule :func:`ronin.telemetry.for_home` follows.
    """

    verb: Verb = Verb.STATUS
    home: Path = Path()
    #: ``show`` prints at most this many payloads, newest last. 0 means every one.
    limit: int = 0


#: What each field is, in one clause. Keyed by the names in
#: :data:`~ronin.telemetry.PAYLOAD_FIELDS`, and :func:`render_fields` fails loudly on a
#: field with no gloss — so adding something to the payload forces a decision about how
#: to describe it to the person it is collected from, instead of shipping it unlabelled.
FIELD_GLOSS: Final[dict[str, str]] = {
    "duration_bucket": "how long the task took, as a coarse bucket (not seconds)",
    "gate_denied": "whether an approval was refused (true/false only)",
    "os_name": "linux, macos or windows",
    "outcome": "solved / failed / gave-up / errored",
    "python_version": "e.g. 3.12",
    "ronin_version": "e.g. 1.0.0",
    "task_category": "which kind of task, from a fixed list",
    "taxonomy_class": "which failure class, when it failed, from the fixed six",
    "turn_bucket": "how many turns, as a coarse bucket (not the exact count)",
}


def render_fields() -> str:
    """The payload field list, derived from the real schema rather than a copy of it.

    Iterating :data:`~ronin.telemetry.PAYLOAD_FIELDS` is what keeps this honest: a
    privacy notice listing yesterday's fields is worse than none, because it is
    evidence that nobody checks. An unglossed field raises rather than printing a bare
    name — the point of the notice is that a reader understands each item.
    """
    missing = [name for name in PAYLOAD_FIELDS if name not in FIELD_GLOSS]
    if missing:
        raise AssertionError(
            f"telemetry payload fields with no description: {missing}. Add them to "
            "FIELD_GLOSS — a field the disclosure cannot explain should not be sent."
        )
    return "\n".join(f"  {name:20} {FIELD_GLOSS[name]}" for name in PAYLOAD_FIELDS)


def run(options: TelemetryOptions) -> tuple[int, str, str]:
    """Do the verb. Returns ``(exit_code, stdout, stderr)``.

    Text out rather than printed, for the same reason ``bench`` does it: a command
    that prints is a command whose output a test can only capture by intercepting a
    stream.
    """
    store = ConsentStore(default_consent_path(options.home))
    log_path = default_log_path(options.home)

    if options.verb is Verb.STATUS:
        return EXIT_OK, _status(store, log_path, options.home), ""
    if options.verb is Verb.ON:
        record = store.grant()
        body = WHAT_IS_SENT.format(fields=render_fields())
        tail = "" if sender_configured(options.home) else f"\n{NO_ENDPOINT}\n"
        return (
            EXIT_OK,
            f"{body}\ntelemetry is ON.{tail}\n{_where(store, log_path)}",
            _problem(record),
        )
    if options.verb is Verb.OFF:
        record = store.refuse()
        return (
            EXIT_OK,
            "telemetry is OFF. Nothing further will be sent.\n"
            f"the local log of what was already sent is kept at {log_path} — "
            "delete it yourself if you want it gone.\n",
            _problem(record),
        )
    return _show(log_path, options.limit)


def _status(store: ConsentStore, log_path: Path, home: Path) -> str:
    record = store.read()
    lines = [f"consent: {record.state.value}"]
    if record.state is Consent.UNASKED:
        lines.append(DISCLOSURE)
    elif record.state is Consent.GRANTED:
        if sender_configured(home):
            lines.append(
                "aggregate task outcomes are being sent. `telemetry off` stops it."
            )
        else:
            # Saying "outcomes are being sent" here would be false in this build, and a
            # privacy surface that overstates what it does is not a safe error to make
            # in either direction.
            lines.append("consent is granted, but " + NO_ENDPOINT[6:])
    else:
        lines.append("nothing is being sent.")
    lines.append(_where(store, log_path).rstrip("\n"))
    if record.problem:
        # Surfaced in the normal output, not only on the error stream: a record that
        # failed to parse resolved to "refused", and the user should know their
        # *choice* was not read rather than assume they chose this.
        lines.append(f"note: {record.problem}")
    return "\n".join(lines) + "\n"


def _where(store: ConsentStore, log_path: Path) -> str:
    sent = "no payloads logged yet" if not log_path.exists() else str(log_path)
    return f"consent file: {store.path}\nsent log:     {sent}\n"


def _problem(record: object) -> str:
    problem = getattr(record, "problem", "")
    return f"note: {problem}\n" if problem else ""


def _show(log_path: Path, limit: int) -> tuple[int, str, str]:
    """Print the append-only log verbatim.

    Verbatim on purpose. Reformatting would introduce a layer that could disagree with
    what was sent, and the entire value of this verb is that it cannot.
    """
    if not log_path.exists():
        return (
            EXIT_OK,
            "no telemetry has been sent from this machine "
            f"(no {log_path}).\n",
            "",
        )
    try:
        lines = [
            line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    except OSError as exc:
        return EXIT_ERROR, "", f"cannot read {log_path}: {exc}\n"

    shown = lines[-limit:] if limit > 0 else lines
    header = (
        f"{len(lines)} payload(s) sent from this machine, exactly as they went out"
        + (f" (showing the last {len(shown)})" if len(shown) != len(lines) else "")
        + f".\n{log_path}\n\n"
    )
    return EXIT_OK, header + "\n".join(shown) + "\n", ""


__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "NO_ENDPOINT",
    "WHAT_IS_SENT",
    "TelemetryOptions",
    "Verb",
    "render_fields",
    "run",
    "sender_configured",
]
