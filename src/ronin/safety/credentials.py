"""Find leaked credentials in text, and report *where* without reporting *what*.

The whole module exists to answer one question — "is there a live key checked into
this tree?" — under one constraint: **the answer must not be another copy of the
key**. A scanner that prints what it found writes the secret into a terminal
scrollback, a CI log, a ticket and whatever pastes the ticket. So a finding carries
a location, a kind, and a masked hint, and :func:`find_secrets` refuses to emit a
hint that fails :func:`revealing`.

Pure and stdlib-only: :mod:`re` and nothing else. Walking a tree, running ``git``
and rendering a table all live in :mod:`ronin.cli.scan`, which is what makes every
match decision here testable with a string.

**Ported from v1, with the safety gate made real.** ``ronin_cli.secret_scan``
documented "the raw matched value is asserted absent from every emitted hint before
returning" and implemented it as ``if match in hint`` — a check that cannot fire,
because a hint always contains an ellipsis the match does not. It was decorative.
The invariant here is the reverse and it is checkable: a hint may reveal at most
:data:`REVEALED` characters from each end, and only when doing so leaves *at least
half* the match elided. :func:`revealing` states it, :func:`find_secrets` enforces
it on every hint, and a hint that fails degrades to the bare kind rather than
shipping.

**Over-reporting is the acceptable failure.** A false positive costs somebody thirty
seconds; a false negative is a key in a public repository. The patterns are still
provider-prefixed rather than entropy-based, because an entropy heuristic over a
source tree flags every base64 blob and gets switched off within a day — and a
switched-off scanner finds nothing at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: ``(kind, pattern)``. Provider prefixes, not entropy: the prefix is what keeps the
#: false-positive rate low enough that the tool stays on. Each tail length is the
#: shortest the issuer actually mints, so a truncated example does not match.
#:
#: Kept deliberately close to the v1 set so what this surfaces and what a commit hook
#: blocks mean the same thing — with one addition. A PEM ``BEGIN`` line is the
#: highest-bleed leak there is (SSH, TLS, signing keys) and the v1 pattern set omitted
#: it because that set was written for inline provider keys. Only the header line is
#: matched, which locates and classifies the leak without the body ever being read
#: into a match object.
PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    ("openai-key", r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    ("stripe-live-sk", r"\bsk_live_[A-Za-z0-9]{20,}"),
    ("stripe-test-sk", r"\bsk_test_[A-Za-z0-9]{20,}"),
    ("stripe-rk", r"\brk_(?:live|test)_[A-Za-z0-9]{20,}"),
    ("stripe-pk", r"\bpk_(?:live|test)_[A-Za-z0-9]{20,}"),
    ("github-pat", r"\bghp_[A-Za-z0-9]{30,}"),
    ("github-oauth", r"\bgh[ousr]_[A-Za-z0-9]{20,}"),
    ("slack-bot", r"\bxoxb-\d+-\d+-[A-Za-z0-9]{20,}"),
    ("slack-user", r"\bxoxp-\d+-\d+-\d+-[A-Za-z0-9]{20,}"),
    ("slack-app", r"\bxapp-\d+-[A-Za-z0-9]+-\d+-[A-Za-z0-9]{20,}"),
    ("aws-akid", r"\bAKIA[0-9A-Z]{16}\b"),
    ("linear-key", r"\blin_api_[A-Za-z0-9]{20,}"),
    ("jwt", r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    ("fernet", r"\bgAAAAA[A-Za-z0-9_\-]{50,}={0,2}"),
    ("notion-secret", r"\bsecret_[A-Za-z0-9]{40,}"),
    ("resend-key", r"\bre_[A-Za-z0-9_]{20,}"),
    ("private-key", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"),
)

_COMPILED: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (kind, re.compile(pattern)) for kind, pattern in PATTERNS
)

#: How many characters a hint may show from each end of a match. Four is enough to
#: recognise which key of several is meant — the provider prefix at the front, the
#: last four at the back, which is how issuers' own dashboards identify a key.
REVEALED: Final = 4

#: A hint shows at most this fraction of a match. At ``2``, half the value must stay
#: elided, which is what stops :data:`REVEALED` from being most of a short match.
ELISION_RATIO: Final = 2

#: Words that mark a documentation value rather than a live key. Compared against the
#: upper-cased match.
#:
#: Deliberately *narrower* than a commit guard's list: the bare-repetition markers a
#: guard also carries (``XXXXXX``, ``1234567890``, ``DEADBEEF``) are absent, because a
#: real high-entropy key can legitimately contain a run of x's or digits and
#: suppressing on that produces a false *negative* — the one failure this tool must
#: not have.
PLACEHOLDER_MARKERS: Final[tuple[str, ...]] = (
    "EXAMPLE",
    "PLACEHOLDER",
    "YOUR_",
    "YOURKEY",
    "YOUR-",
    "REDACTED",
    "TEST_TOKEN",
    "FAKEKEY",
    "DUMMY",
    "CHANGEME",
    "CHANGE_ME",
    "SAMPLE",
    "NOTAREAL",
    "INSERT_",
    "<YOUR",
)

#: Put one of these on the line to suppress a known-safe value. Three spellings
#: because a repository that already uses ``detect-secrets`` should not have to learn
#: a fourth pragma to silence the same line twice.
ALLOW_PRAGMAS: Final[tuple[str, ...]] = (
    "ronin:allow-secret",
    "pragma: allowlist secret",
    "noqa: secret",
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One credential, located but not disclosed.

    ``hint`` is a masked fragment (``sk-a…f00d``) or, when masking would reveal too
    much of a short value, the bare ``kind``. It is never the match.
    """

    path: str
    line: int
    kind: str
    hint: str

    #: Set for a history finding; empty for a working-tree one. Carried here rather
    #: than in a second type because every renderer, sorter and JSON encoder would
    #: otherwise need to know which of two shapes it held.
    commit: str = ""


def revealing(hint: str, match: str) -> bool:
    """True when ``hint`` discloses too much of ``match`` to be safe to print.

    The invariant the v1 scanner claimed and did not have. Three ways a hint fails:
    it is the match, it contains the match, or it shows more than
    ``2 * REVEALED`` characters of a match shorter than ``ELISION_RATIO`` times that.
    A bare kind (``"aws-akid"``) passes trivially, which is the point — it is the
    fallback :func:`find_secrets` degrades to.
    """
    if hint == match or match in hint:
        return True
    # Counted per character and by membership, not as a substring: a hint that
    # rearranged or repeated the match's characters would still be disclosing them,
    # and this over-counts rather than under-counts in every such case. Over-counting
    # costs a hint that degrades to its kind; under-counting costs a leak.
    shown = sum(1 for char in hint if char in match)
    return shown * ELISION_RATIO > len(match)


def mask(kind: str, match: str) -> str:
    """A hint that locates a secret without disclosing it.

    ``sk-a…9f2c`` shaped: :data:`REVEALED` characters from each end and an ellipsis
    for everything between. Returns the bare ``kind`` when the match is too short for
    that to leave half of it elided, so a short value is named rather than sampled.

    The shortest thing the pattern set matches is an AWS access key id at twenty
    characters, which is sampled — and four of the eight characters it shows are the
    fixed ``AKIA`` prefix, so four of sixteen variable characters are disclosed. That
    is the floor, and it is the same fragment AWS's own console prints to identify a
    key.
    """
    if len(match) < 2 * REVEALED * ELISION_RATIO:
        return kind
    hint = f"{match[:REVEALED]}…{match[-REVEALED:]}"
    return kind if revealing(hint, match) else hint


def suppressed(match: str, line: str) -> bool:
    """True for an obvious placeholder, or a line carrying an allow-pragma."""
    upper = match.upper()
    if any(marker in upper for marker in PLACEHOLDER_MARKERS):
        return True
    lowered = line.lower()
    return any(pragma in lowered for pragma in ALLOW_PRAGMAS)


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _line_text(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start : end if end != -1 else len(text)]


def find_secrets(text: str, path: str) -> list[Finding]:
    """Every credential in ``text``, as :class:`Finding` sorted by ``(line, kind)``.

    Overlapping matches are resolved earliest-start-then-longest and the loser is
    dropped, so one key that satisfies two patterns is reported once rather than
    twice under two names.

    No I/O: ``path`` is carried through verbatim for the caller to make relative,
    absolute or a git path as it sees fit.
    """
    if not text:
        return []

    spans: list[tuple[int, int, str]] = []
    for kind, pattern in _COMPILED:
        spans.extend((found.start(), found.end(), kind) for found in pattern.finditer(text))
    spans.sort(key=lambda span: (span[0], -span[1]))

    findings: list[Finding] = []
    consumed = -1
    for start, end, kind in spans:
        if start < consumed:
            continue
        match = text[start:end]
        if suppressed(match, _line_text(text, start)):
            consumed = end
            continue
        hint = mask(kind, match)
        # The gate, not a comment about one: a hint that would disclose too much is
        # replaced rather than emitted. `mask` already checks, so reaching this is a
        # bug in `mask` — which is exactly when an assertion of the invariant is
        # worth its cost, because the failure it catches is a leak.
        findings.append(
            Finding(
                path=path,
                line=_line_number(text, start),
                kind=kind,
                hint=kind if revealing(hint, match) else hint,
            )
        )
        consumed = end

    findings.sort(key=lambda finding: (finding.line, finding.kind))
    return findings


_COMMIT_RE: Final = re.compile(r"^commit ([0-9a-f]{7,40})\b")
_HUNK_RE: Final = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def find_secrets_in_diff(diff: str) -> list[Finding]:
    """Credentials on *added* lines of ``git log -p`` output, with their commit.

    Deleting a key and committing the deletion does not remove it: the blob stays in
    the pack and ``git show`` prints it back. So a working-tree scan can be clean on a
    repository that is still leaking, and this is the half that says so.

    Pure — the caller runs git. Tracks the post-image path from ``+++ b/<path>`` and
    the new-file line from each hunk header, advancing on context and added lines and
    not on removed ones, so a reported line number is the number in the commit that
    introduced it.
    """
    findings: list[Finding] = []
    commit = ""
    path = ""
    line_number = 0

    for raw in diff.splitlines():
        header = _COMMIT_RE.match(raw)
        if header:
            commit, path, line_number = header.group(1), "", 0
            continue
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target.startswith("b/"):
                path = target[2:]
            else:
                # `/dev/null` as the post-image means the commit deleted the file. An
                # empty path then suppresses every line of the hunk, which is right:
                # those are removals, and a removal cannot introduce a key.
                path = "" if target == "/dev/null" else target
            continue
        if raw.startswith(("--- ", "diff --git")):
            continue
        hunk = _HUNK_RE.match(raw)
        if hunk:
            line_number = int(hunk.group(1))
            continue
        if line_number == 0 or not path:
            continue
        if raw.startswith("+"):
            findings.extend(
                Finding(path=path, line=line_number, kind=hit.kind, hint=hit.hint, commit=commit)
                for hit in find_secrets(raw[1:], path)
            )
            line_number += 1
        elif not raw.startswith("-"):
            line_number += 1

    findings.sort(key=lambda f: (f.commit, f.path, f.line, f.kind))
    return findings


__all__ = [
    "ALLOW_PRAGMAS",
    "ELISION_RATIO",
    "PATTERNS",
    "PLACEHOLDER_MARKERS",
    "REVEALED",
    "Finding",
    "find_secrets",
    "find_secrets_in_diff",
    "mask",
    "revealing",
    "suppressed",
]
