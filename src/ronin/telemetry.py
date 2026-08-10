"""opt-in telemetry: aggregate task outcomes, and structurally nothing else.

This is the most privacy-sensitive module in the repository, so it is built so that
sending something forbidden is **impossible rather than merely discouraged**. Three
mechanisms do that work, and none of them is a convention someone has to remember:

1. **The payload is a closed set of primitives.** :class:`TaskOutcome` is a frozen
   dataclass whose every field is a bounded ``StrEnum``, a validated version string,
   or a ``bool``. There is no ``str`` a caller can fill with whatever they like, no
   ``dict[str, Any]``, no ``notes``, no ``extra``. A prompt, a file path, a
   filename, a diff, a command, a repository name, a branch name and a code excerpt
   are all *unrepresentable* — there is no field they would fit in.
2. **The serialized key set is an allowlist with a test behind it.**
   :data:`PAYLOAD_FIELDS` is the exact key set :func:`serialize` emits, and
   ``tests/telemetry`` asserts the serialized JSON keys equal it. Adding a field is
   therefore a deliberate, test-breaking act that a reviewer sees.
3. **Consent defaults to off and the transport is injected.** :class:`Telemetry`
   never imports ``httpx``, never opens a socket and never reads ``os.environ``. It
   calls a :data:`Sender` you hand it, and only when :class:`Consent` is
   ``GRANTED``.

What is deliberately **not** collected, and why
-----------------------------------------------

*Prompts and model output.* The entire point of the product is that people type
their real work into it. A prompt is the crown jewels: it contains the business
problem, often a customer name, sometimes a secret. There is no aggregation of
prompt text that is safe, so there is no field for it.

*Code, diffs, filenames and paths.* A path leaks the repository layout and often
the employer; a filename leaks the feature under development
(``billing/dunning_v2.py``); a diff is the code. None of these have a bounded
domain, so none of them can be a field here.

*Commands.* ``bash`` arguments contain hostnames, ticket ids, credentials typed by
mistake, and the shape of someone's infrastructure.

*Task ids.* Ronin's task and eval-case ids are **user-authored** — a slash command
is named by whoever wrote the markdown file, and an eval case id is written by
whoever wrote the fixture. "fix-acme-corp-sso-timeout" is a customer name and a
security incident in one string. A user-authored identifier is free text wearing an
identifier's clothes, so there is no ``task_id`` field. The consequence is accepted:
two outcomes from the same task cannot be correlated, and the aggregate is a
histogram rather than a join.

*Any machine, user, install or session identifier.* No uuid is minted here. Without
one, two payloads from the same machine are indistinguishable from two payloads from
two machines — which costs the ability to compute per-user rates, and buys the
inability to build a per-user profile. That trade is made on purpose.

*Timestamps.* A precise send time is a weak identifier and a strong correlator (it
pins a person's working hours and, joined with a git history, a person). The
duration of the turn is bucketed; when it happened is not recorded.

*Exact counts and durations.* Turn counts and wall-clock times are reported as
buckets (:class:`TurnBucket`, :class:`DurationBucket`) because an exact
``1_247.318s`` is a near-unique fingerprint while ``1-5m`` answers the only question
anyone actually asks of it.

*Exact python and Ronin patch versions.* ``python_version`` is ``major.minor``
only. A patch version narrows a population fast and answers nothing.

Consent
-------

Three states, in :class:`Consent`: ``UNASKED``, ``GRANTED``, ``REFUSED``. The
default is ``UNASKED``, and ``UNASKED`` sends nothing — there is no "on until you
say no". A one-line disclosure (:data:`DISCLOSURE`) is shown once, recorded, and
never shown again; :meth:`Telemetry.disclosure_once` is the only thing that returns
it, and it returns ``None`` on every subsequent call. **Refusal is sticky**: once
``REFUSED`` is written nothing re-prompts, ever, and the only way back is an
explicit opt-in command.

A consent file that cannot be read or parsed resolves to ``REFUSED`` with
``disclosed=True`` and a :attr:`ConsentRecord.problem` naming the fault. That is
fail-closed in both directions at once: a corrupted file can neither cause a send
nor cause a re-prompt. It means a user who had opted in silently stops sending after
disk corruption, which is the safe direction to fail in.

Auditing rather than trusting
-----------------------------

:class:`PayloadLog` appends the exact wire JSON of every payload handed to the
sender, one object per line, to a local file the user owns. ``ronin telemetry show``
prints it. A user should not have to believe this docstring; they should be able to
read the bytes. The line is written *before* the send is attempted, so a crash
mid-send still leaves the record — which means a logged line proves the payload was
handed to the sender, not that it arrived. That asymmetry is the honest one: the log
can over-report what left the machine, never under-report it.

Never breaking a turn
---------------------

Telemetry that can break a session is worse than no telemetry. :meth:`Telemetry.record`
returns a :class:`RecordResult` and never raises: every failure of the sender, the
log or the consent file is caught and turned into ``RecordResult.FAILED``. The send
is bounded by :data:`SEND_TIMEOUT_SECONDS` so a hung endpoint cannot stall a turn,
and callers are expected to call ``record`` *after* the user already has their
answer.

No hidden state
---------------

Every path on disk is injected (:class:`ConsentStore`, :class:`PayloadLog` both take
a ``Path``); there is no module-level mutable state and no environment read. The one
impure function is :func:`local_environment`, named so it is obvious, and it reads
``sys`` and the installed package version — not ``os.environ``.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

# --------------------------------------------------------------------------- #
# The closed vocabulary
# --------------------------------------------------------------------------- #


class TaskCategory(StrEnum):
    """What kind of work the turn was, coarsely. A closed set on purpose.

    Coarse enough that no member identifies a project. ``OTHER`` exists so a caller
    that cannot classify a turn has somewhere to put it that is not a free-text
    field — the escape hatch has to be inside the enum, or someone will add a
    ``category_detail: str`` next to it.
    """

    EDIT = "edit"
    FIX = "fix"
    EXPLAIN = "explain"
    SEARCH = "search"
    REFACTOR = "refactor"
    TEST = "test"
    REVIEW = "review"
    OTHER = "other"


class Outcome(StrEnum):
    """How the turn ended. Mirrors the exit-code contract, not the model's text.

    The three CLI exit codes (``0`` done, ``1`` error, ``2`` needs approval) are all
    representable, plus the two ways a turn stops without being either.
    """

    DONE = "done"
    ERROR = "error"
    NEEDS_APPROVAL = "needs_approval"
    INTERRUPTED = "interrupted"
    BUDGET_EXHAUSTED = "budget_exhausted"


class TaxonomyClass(StrEnum):
    """Whose fault the failure was, in the one dimension that changes what you do.

    The whole reason a failure taxonomy exists is to answer "fix the harness or fix
    the model", so these are the classes that decision needs and nothing finer.
    Deliberately *not* imported from ``ronin.evals.taxonomy``: that module is free to
    grow richer classes, including ones carrying explanatory text, and this enum must
    stay a closed set of labels no matter what happens over there. A mapping from one
    to the other belongs at the call site, where a reviewer can see it.
    """

    NONE = "none"
    TOOL_SYNTAX = "tool_syntax"
    GATE_BEHAVIOUR = "gate_behaviour"
    RECOVERY = "recovery"
    PLANNING = "planning"
    HARNESS = "harness"
    MODEL_CAPABILITY = "model_capability"
    UNCLASSIFIED = "unclassified"


class OsName(StrEnum):
    """Which family of operating system. Four values, because the reason to know is
    that shells, path separators and sandbox backends differ by family."""

    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"
    OTHER = "other"


class TurnBucket(StrEnum):
    """Iterations the turn took, bucketed. Values are what goes on the wire."""

    ONE = "1"
    TWO_TO_THREE = "2-3"
    FOUR_TO_SEVEN = "4-7"
    EIGHT_TO_FIFTEEN = "8-15"
    SIXTEEN_PLUS = "16+"


class DurationBucket(StrEnum):
    """Wall-clock time the turn took, bucketed. Values are what goes on the wire."""

    UNDER_1S = "<1s"
    ONE_TO_5S = "1-5s"
    FIVE_TO_15S = "5-15s"
    FIFTEEN_TO_60S = "15-60s"
    ONE_TO_5M = "1-5m"
    OVER_5M = ">5m"


#: Bucket boundaries as ``(exclusive upper bound in turns, bucket)``, ascending. A
#: table rather than a chain of ``if``s so the boundaries are readable as data and
#: the test can walk them.
TURN_BOUNDS: tuple[tuple[int, TurnBucket], ...] = (
    (2, TurnBucket.ONE),
    (4, TurnBucket.TWO_TO_THREE),
    (8, TurnBucket.FOUR_TO_SEVEN),
    (16, TurnBucket.EIGHT_TO_FIFTEEN),
)

#: Bucket boundaries as ``(exclusive upper bound in seconds, bucket)``, ascending.
DURATION_BOUNDS: tuple[tuple[float, DurationBucket], ...] = (
    (1.0, DurationBucket.UNDER_1S),
    (5.0, DurationBucket.ONE_TO_5S),
    (15.0, DurationBucket.FIVE_TO_15S),
    (60.0, DurationBucket.FIFTEEN_TO_60S),
    (300.0, DurationBucket.ONE_TO_5M),
)


def turn_bucket(turns: int) -> TurnBucket:
    """Bucket an iteration count. Half-open ``[lo, hi)``, so ``4`` is ``4-7``.

    Raises on ``turns < 1``: a turn that ran zero iterations did not happen, and
    silently bucketing it as "1" would put a harness bug into the aggregate as a
    successful short turn.
    """
    if turns < 1:
        raise ValueError(f"turns must be >= 1, got {turns}")
    for upper, bucket in TURN_BOUNDS:
        if turns < upper:
            return bucket
    return TurnBucket.SIXTEEN_PLUS


def duration_bucket(seconds: float) -> DurationBucket:
    """Bucket a wall-clock duration. Half-open ``[lo, hi)``, so ``5.0`` is ``5-15s``.

    Raises on a negative duration rather than clamping. Durations come from a
    monotonic clock and cannot be negative; one that is means the caller subtracted
    in the wrong order, and clamping would hide that inside the least-inspected data
    path in the repository.
    """
    if seconds < 0:
        raise ValueError(f"seconds must be >= 0, got {seconds}")
    for upper, bucket in DURATION_BOUNDS:
        if seconds < upper:
            return bucket
    return DurationBucket.OVER_5M


# --------------------------------------------------------------------------- #
# The payload
# --------------------------------------------------------------------------- #

#: What a serialized field may be. There is no ``int``, no ``float`` and no nested
#: container: a number is a bucket label here, and a container is where a free-text
#: field eventually gets smuggled in.
PayloadValue = str | bool

#: Stand-in for a version that could not be determined. Not a made-up number.
UNKNOWN_VERSION = "unknown"

#: A Ronin version is dotted digits or :data:`UNKNOWN_VERSION`. Enforced rather than
#: trusted, because ``ronin_version`` is the only field a caller supplies as a string
#: and therefore the only field a prompt could ever be smuggled through.
_RONIN_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}$")

#: ``major.minor`` exactly. A patch version narrows the population and answers
#: nothing anyone asks.
_PYTHON_VERSION_RE = re.compile(r"^\d+\.\d+$")

#: The exact key set :func:`serialize` emits, sorted the way ``json.dumps(...,
#: sort_keys=True)`` emits it. ``tests/telemetry`` asserts equality against the real
#: serialized output, so adding a field to :class:`TaskOutcome` fails a test until
#: someone edits this tuple on purpose.
PAYLOAD_FIELDS: tuple[str, ...] = (
    "duration_bucket",
    "gate_denied",
    "os_name",
    "outcome",
    "python_version",
    "ronin_version",
    "task_category",
    "taxonomy_class",
    "turn_bucket",
)


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """One turn's outcome, as the closed set of primitives that may leave a machine.

    Every field is an enum, a pattern-validated version string, or a bool. That is
    the whole guarantee: there is no member of this type that can hold a prompt, a
    path, a filename, a command, a diff, an identifier or a timestamp, so no call
    site can accidentally put one there and no reviewer has to check for it.
    """

    ronin_version: str
    python_version: str
    os_name: OsName
    task_category: TaskCategory
    outcome: Outcome
    taxonomy_class: TaxonomyClass
    turn_bucket: TurnBucket
    duration_bucket: DurationBucket
    gate_denied: bool

    def __post_init__(self) -> None:
        if self.ronin_version != UNKNOWN_VERSION and not _RONIN_VERSION_RE.match(
            self.ronin_version
        ):
            raise ValueError(
                f"ronin_version {self.ronin_version!r} is not dotted digits or "
                f"{UNKNOWN_VERSION!r}. This field is pattern-checked because it is the "
                "only free-form string on the payload, and an unchecked string is how "
                "a path or a prompt fragment eventually ships."
            )
        if not _PYTHON_VERSION_RE.match(self.python_version):
            raise ValueError(
                f"python_version {self.python_version!r} must be 'major.minor' — the "
                "patch level is deliberately dropped, see the module docstring"
            )


def serialize(payload: TaskOutcome) -> dict[str, PayloadValue]:
    """The payload as a plain dict whose keys are exactly :data:`PAYLOAD_FIELDS`.

    Written out field by field rather than via ``dataclasses.asdict``: a reflective
    serializer silently gains a key the moment someone adds a field, which is exactly
    the event that must not be silent.
    """
    return {
        "duration_bucket": payload.duration_bucket.value,
        "gate_denied": payload.gate_denied,
        "os_name": payload.os_name.value,
        "outcome": payload.outcome.value,
        "python_version": payload.python_version,
        "ronin_version": payload.ronin_version,
        "task_category": payload.task_category.value,
        "taxonomy_class": payload.taxonomy_class.value,
        "turn_bucket": payload.turn_bucket.value,
    }


def to_json(payload: TaskOutcome) -> str:
    """One payload as one line of JSON. ``sort_keys`` so two payloads diff cleanly."""
    return json.dumps(serialize(payload), sort_keys=True, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Where the environment fields come from
# --------------------------------------------------------------------------- #


def os_name_for(platform: str) -> OsName:
    """Classify a ``sys.platform`` string into the four families.

    Takes the string rather than reading ``sys.platform`` so the mapping is testable
    on one machine, which is the only way to know that ``win32`` is handled.
    """
    if platform.startswith("linux"):
        return OsName.LINUX
    if platform == "darwin":
        return OsName.MACOS
    if platform.startswith(("win32", "cygwin", "msys")):
        return OsName.WINDOWS
    return OsName.OTHER


def normalize_ronin_version(raw: str) -> str:
    """A version string reduced to something the payload will accept.

    ``importlib.metadata`` returns whatever the installed metadata says, and a source
    checkout returns a sentence (see ``ronin.cli.main._version``). Anything that is
    not dotted digits becomes :data:`UNKNOWN_VERSION` rather than travelling as-is —
    a local dev version like ``1.0.0+dirty.git.acme-internal`` would otherwise carry
    a branch name onto the wire.
    """
    return raw if _RONIN_VERSION_RE.match(raw) else UNKNOWN_VERSION


@dataclass(frozen=True, slots=True)
class Environment:
    """The three environment fields, resolved once. Values, not lookups."""

    ronin_version: str
    python_version: str
    os_name: OsName


def describe_environment(
    *, ronin_version: str, python_version: str, platform: str
) -> Environment:
    """Build an :class:`Environment` from raw strings. Pure, so it is testable."""
    return Environment(
        ronin_version=normalize_ronin_version(ronin_version),
        python_version=python_version,
        os_name=os_name_for(platform),
    )


def local_environment() -> Environment:
    """This process's environment. The one impure function in the module.

    Reads ``sys.version_info``, ``sys.platform`` and the installed package version —
    and nothing else. In particular it does not read ``os.environ``, does not look at
    the current directory, and does not touch git.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("ronin")
    except PackageNotFoundError:
        installed = UNKNOWN_VERSION
    return describe_environment(
        ronin_version=installed,
        python_version=f"{sys.version_info[0]}.{sys.version_info[1]}",
        platform=sys.platform,
    )


def outcome_for_exit_code(code: int) -> Outcome:
    """Map the CLI's exit code onto an :class:`Outcome`.

    ``0`` done, ``1`` error, ``2`` an approval was requested and denied — the
    contract in ``ronin.ui.headless``. Anything else is an error, because an exit
    code this layer does not recognize is not a success.
    """
    if code == 0:
        return Outcome.DONE
    if code == 2:
        return Outcome.NEEDS_APPROVAL
    return Outcome.ERROR


def outcome_payload(
    environment: Environment,
    *,
    task_category: TaskCategory,
    outcome: Outcome,
    turns: int,
    seconds: float,
    taxonomy_class: TaxonomyClass = TaxonomyClass.NONE,
    gate_denied: bool = False,
) -> TaskOutcome:
    """Assemble a payload from a turn's raw facts, bucketing as it goes.

    The only constructor call sites should use: it takes the exact turn count and the
    exact duration and *loses* them here, so no caller has to remember to bucket and
    none of them can pass the precise values through by mistake.
    """
    return TaskOutcome(
        ronin_version=environment.ronin_version,
        python_version=environment.python_version,
        os_name=environment.os_name,
        task_category=task_category,
        outcome=outcome,
        taxonomy_class=taxonomy_class,
        turn_bucket=turn_bucket(turns),
        duration_bucket=duration_bucket(seconds),
        gate_denied=gate_denied,
    )


# --------------------------------------------------------------------------- #
# Consent
# --------------------------------------------------------------------------- #


class Consent(StrEnum):
    """Whether the user has been asked, and what they said.

    ``UNASKED`` is not a soft yes. Nothing is sent in any state but ``GRANTED``.
    """

    UNASKED = "unasked"
    GRANTED = "granted"
    REFUSED = "refused"


#: Bumped if the on-disk shape ever changes. An unknown schema is treated the same
#: way as a corrupt file: fail closed, do not re-prompt.
CONSENT_SCHEMA = 1

#: Where the consent record lives, relative to the *home* directory. Per user rather
#: than per project, because a user who refused in one repository must not be asked
#: again in the next one — a refusal that only sticks per-checkout is not a refusal.
CONSENT_RELATIVE_PATH = Path(".ronin") / "telemetry.json"

#: Where the audit log lives, relative to home. Next to the consent record so
#: "everything telemetry knows about me" is two files in one directory.
SENT_LOG_RELATIVE_PATH = Path(".ronin") / "telemetry-sent.jsonl"

#: The one line shown at first run, once. Kept as a constant so the README, the
#: docs site and the CLI cannot each paraphrase it into a different promise.
DISCLOSURE = (
    "telemetry is OFF and nothing has been sent: `ronin2 telemetry on` opts "
    "in to aggregate task outcomes only — never prompts, code, paths, filenames, "
    "commands or identifiers — `ronin2 telemetry show` prints every payload "
    "sent, and `ronin2 telemetry off` stops it."
)


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    """The consent state plus whether the disclosure has already been shown.

    ``problem`` is local-only diagnostic text explaining why a record resolved the
    way it did. It is never serialized into a payload — :func:`serialize` cannot
    reach it, because :class:`TaskOutcome` has no field it could go in.
    """

    state: Consent = Consent.UNASKED
    disclosed: bool = False
    problem: str = ""

    @property
    def may_send(self) -> bool:
        """The single question the transport asks. ``GRANTED`` and nothing else."""
        return self.state is Consent.GRANTED


@dataclass(frozen=True, slots=True)
class ConsentStore:
    """The consent record on disk. Path injected; no default location is assumed.

    Frozen because the store is not the state — the file is. Every mutator reads,
    replaces and writes, and returns the record it wrote, so a caller never has to
    guess whether an in-memory copy is current.
    """

    path: Path

    def read(self) -> ConsentRecord:
        """The record, or a fail-closed one. Never raises.

        A missing file is a genuine ``UNASKED``. Anything else that goes wrong —
        unreadable, not JSON, not an object, an unknown schema, an unknown state —
        resolves to ``REFUSED`` with ``disclosed=True``: it can neither cause a send
        nor cause a re-prompt. A user who had opted in stops being recorded, which is
        the direction it is safe to fail in.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ConsentRecord()
        except OSError as exc:
            return self._closed(f"{self.path} could not be read ({exc.strerror or exc})")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return self._closed(f"{self.path} is not valid JSON ({exc.msg})")
        if not isinstance(data, dict):
            return self._closed(
                f"{self.path} must contain a JSON object, found {type(data).__name__}"
            )
        if data.get("schema") != CONSENT_SCHEMA:
            return self._closed(
                f"{self.path} declares schema {data.get('schema')!r}, and this build "
                f"understands {CONSENT_SCHEMA}"
            )
        raw_state = data.get("state")
        if not isinstance(raw_state, str) or raw_state not in {s.value for s in Consent}:
            return self._closed(f"{self.path} has an unknown state {raw_state!r}")
        return ConsentRecord(
            state=Consent(raw_state),
            disclosed=bool(data.get("disclosed", False)),
        )

    def _closed(self, problem: str) -> ConsentRecord:
        return ConsentRecord(state=Consent.REFUSED, disclosed=True, problem=problem)

    def write(self, record: ConsentRecord) -> ConsentRecord:
        """Persist ``record``. Never raises; a failure comes back as ``problem``.

        A write that fails means the answer is not remembered for next time. For a
        refusal that costs one extra prompt; for a grant it costs the opt-in. Neither
        is worth an exception out of a module the session calls on its way out, so the
        failure travels as data.
        """
        payload = {
            "schema": CONSENT_SCHEMA,
            "state": record.state.value,
            "disclosed": record.disclosed,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
            )
        except OSError as exc:
            return replace(
                record,
                problem=f"{self.path} could not be written ({exc.strerror or exc})",
            )
        return replace(record, problem="")

    def grant(self) -> ConsentRecord:
        """Opt in. Also marks the disclosure shown: an explicit yes has seen it."""
        return self.write(ConsentRecord(state=Consent.GRANTED, disclosed=True))

    def refuse(self) -> ConsentRecord:
        """Opt out, permanently. Nothing in this module re-prompts after this."""
        return self.write(ConsentRecord(state=Consent.REFUSED, disclosed=True))

    def mark_disclosed(self) -> ConsentRecord:
        """Record that the one-line disclosure has been shown, keeping the state.

        Separate from :meth:`grant` because showing a notice is not consent. This is
        what makes the disclosure appear exactly once while telemetry stays off.
        """
        return self.write(replace(self.read(), disclosed=True, problem=""))


# --------------------------------------------------------------------------- #
# The local audit log
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PayloadLog:
    """Append-only local record of every payload handed to the sender.

    Exists so a user can audit rather than trust. One JSON object per line, exactly
    the wire form, so ``ronin telemetry show`` needs no interpretation layer that
    could disagree with what was sent.
    """

    path: Path

    def append(self, payload: TaskOutcome) -> str:
        """Write one line. Returns the problem, or ``""`` on success. Never raises."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(to_json(payload) + "\n")
        except OSError as exc:
            return f"{self.path} could not be appended to ({exc.strerror or exc})"
        return ""

    def lines(self) -> tuple[str, ...]:
        """Every logged payload, as the raw lines. Empty when nothing was ever sent."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return ()
        return tuple(line for line in text.splitlines() if line.strip())

    def render(self) -> str:
        """What ``ronin telemetry show`` prints, including the empty case.

        The empty case is the important one: a user who suspects data is leaving
        needs "nothing has been sent" to be a sentence the tool will actually say.
        """
        found = self.lines()
        if not found:
            return f"nothing has been sent. (audit log: {self.path})\n"
        head = f"{len(found)} payload(s) sent, oldest first. (audit log: {self.path})\n"
        return head + "".join(line + "\n" for line in found)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #

#: How a payload leaves the machine, injected. This module has no idea what HTTP is
#: and must not learn: an import of ``httpx`` in here is a socket in the default
#: install and a mocked network in every test that touches a session.
Sender = Callable[[Mapping[str, PayloadValue]], Awaitable[None]]

#: A send that has not finished by now is abandoned. Telemetry must not be able to
#: add latency to a turn; two seconds is already longer than anyone should wait for
#: a metric they cannot see.
SEND_TIMEOUT_SECONDS = 2.0


class RecordResult(StrEnum):
    """What :meth:`Telemetry.record` did. A value, because it must never raise."""

    NO_CONSENT = "no_consent"
    NO_SENDER = "no_sender"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Telemetry:
    """The whole opt-in surface: disclose once, send only when granted.

    Holds no state of its own — the consent record and the audit log are files, and
    both paths are injected. Construct one per run; there is no module-level
    singleton to reach for, on purpose.
    """

    consent: ConsentStore
    sender: Sender | None = None
    log: PayloadLog | None = None
    timeout: float = SEND_TIMEOUT_SECONDS

    def state(self) -> ConsentRecord:
        """The current consent record, read fresh from disk."""
        return self.consent.read()

    def disclosure_once(self) -> str | None:
        """:data:`DISCLOSURE` the first time, ``None`` every time after.

        Marks the record disclosed before returning, so a caller that shows the line
        and then crashes does not show it again next run — an "opt in?" notice that
        reappears is indistinguishable from nagging, and nagging is how a privacy
        notice gets ignored.
        """
        record = self.consent.read()
        if record.disclosed:
            return None
        self.consent.mark_disclosed()
        return DISCLOSURE

    async def record(self, payload: TaskOutcome) -> RecordResult:
        """Send one payload if and only if consent was granted. Never raises.

        Order matters: consent first (so a refused user's payload is never even
        serialized), then the audit log (so the record survives a crash inside the
        sender), then the bounded send. A sender that raises, hangs or returns
        garbage produces ``FAILED`` and nothing else — the caller is a session on its
        way out and has nothing useful to do with an exception.
        """
        if not self.consent.read().may_send:
            return RecordResult.NO_CONSENT
        if self.sender is None:
            return RecordResult.NO_SENDER
        if self.log is not None:
            self.log.append(payload)
        try:
            await asyncio.wait_for(self.sender(serialize(payload)), timeout=self.timeout)
        # Deliberately broad: the sender is caller-supplied and third-party-shaped, and
        # there is no exception from it that justifies breaking the user's session.
        # ``CancelledError`` is a BaseException and so still propagates, which is
        # correct — a cancelled turn should not be held up finishing a metric.
        except Exception:
            return RecordResult.FAILED
        return RecordResult.SENT


def default_consent_path(home: Path) -> Path:
    """``<home>/.ronin/telemetry.json``. A function so no caller hardcodes the join."""
    return home / CONSENT_RELATIVE_PATH


def default_log_path(home: Path) -> Path:
    """``<home>/.ronin/telemetry-sent.jsonl``."""
    return home / SENT_LOG_RELATIVE_PATH


def for_home(home: Path, *, sender: Sender | None = None) -> Telemetry:
    """A :class:`Telemetry` wired to the conventional per-user paths under ``home``.

    ``home`` is a parameter, never ``Path.home()``, so a test describes a machine in
    ``tmp_path`` and nothing writes to the developer's real home directory.
    """
    return Telemetry(
        consent=ConsentStore(default_consent_path(home)),
        sender=sender,
        log=PayloadLog(default_log_path(home)),
    )


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #


async def demo() -> int:
    """Show the three consent states against a recording sender, in a temp dir.

    Offline by construction: the "sender" appends to a list. Run it with
    ``PYTHONPATH=src python -m ronin.telemetry``.
    """
    import tempfile

    captured: list[Mapping[str, PayloadValue]] = []

    async def capture(fields: Mapping[str, PayloadValue]) -> None:
        captured.append(fields)

    with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        telemetry = for_home(home, sender=capture)
        payload = outcome_payload(
            describe_environment(
                ronin_version="1.0.0", python_version="3.11", platform="linux"
            ),
            task_category=TaskCategory.FIX,
            outcome=Outcome.DONE,
            turns=6,
            seconds=42.0,
        )

        print("the payload, in full — this is every field that can ever be sent:")
        print(f"  {to_json(payload)}")
        print(f"  keys == PAYLOAD_FIELDS: {tuple(sorted(serialize(payload))) == PAYLOAD_FIELDS}")

        print("\nfirst run: the disclosure is shown once, and nothing is sent.")
        print(f"  disclosure: {telemetry.disclosure_once()}")
        print(f"  shown again? {telemetry.disclosure_once()!r}")
        print(f"  record() -> {(await telemetry.record(payload)).value}")
        print(f"  sender called {len(captured)} time(s)")

        print("\nrefusal is sticky: still nothing sent, and no second disclosure.")
        telemetry.consent.refuse()
        print(f"  record() -> {(await telemetry.record(payload)).value}")
        print(f"  disclosure: {telemetry.disclosure_once()!r}")

        print("\nafter an explicit opt-in, exactly one payload goes to the sender.")
        telemetry.consent.grant()
        print(f"  record() -> {(await telemetry.record(payload)).value}")
        print(f"  sender called {len(captured)} time(s)")
        assert telemetry.log is not None
        print("\nthe local audit log — what a user reads instead of trusting us:")
        for line in telemetry.log.render().splitlines():
            print(f"  {line}")

        print("\na sender that raises does not propagate:")
        async def broken(fields: Mapping[str, PayloadValue]) -> None:
            raise RuntimeError("endpoint on fire")

        broken_telemetry = replace(telemetry, sender=broken)
        print(f"  record() -> {(await broken_telemetry.record(payload)).value}")
    return 0


__all__ = [
    "CONSENT_RELATIVE_PATH",
    "CONSENT_SCHEMA",
    "DISCLOSURE",
    "DURATION_BOUNDS",
    "PAYLOAD_FIELDS",
    "SEND_TIMEOUT_SECONDS",
    "SENT_LOG_RELATIVE_PATH",
    "TURN_BOUNDS",
    "UNKNOWN_VERSION",
    "Consent",
    "ConsentRecord",
    "ConsentStore",
    "DurationBucket",
    "Environment",
    "OsName",
    "Outcome",
    "PayloadLog",
    "PayloadValue",
    "RecordResult",
    "Sender",
    "TaskCategory",
    "TaskOutcome",
    "TaxonomyClass",
    "Telemetry",
    "TurnBucket",
    "default_consent_path",
    "default_log_path",
    "demo",
    "describe_environment",
    "duration_bucket",
    "for_home",
    "local_environment",
    "normalize_ronin_version",
    "os_name_for",
    "outcome_for_exit_code",
    "outcome_payload",
    "serialize",
    "to_json",
    "turn_bucket",
]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(demo()))
