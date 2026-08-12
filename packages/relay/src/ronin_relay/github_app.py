"""GitHub-App webhook → a gated Ronin run request. Pure, offline, values-not-exceptions.

This is the RH product-surface piece that lets a GitHub App turn a comment or a pull
request into a Ronin run. It deliberately stops at the *decision*: it verifies the
webhook is really from GitHub, decides whether the event should start a run, and builds a
typed :class:`RunRequest` — then hands that to an injected ``enqueue`` callable and returns.
It does **not** run the agent, talk to GitHub's API, or own a queue. That boundary is what
keeps it testable with no network: everything here is a pure function of ``(secret,
headers, body)``, and the one side effect is a callback the caller supplies.

Three rules carry the security, and each is a value returned, not an exception raised
across the boundary (a webhook handler that raises is a 500 and a retry storm):

1. **A request with a bad signature never becomes a run.** GitHub signs the raw body with
   the app's webhook secret; :func:`verify_signature` recomputes the HMAC-SHA256 and
   compares in constant time. No signature, wrong secret, malformed header → ``REJECTED``,
   and ``enqueue`` is never called.
2. **A bot's own comment never becomes a run.** The most important *ignore*: without it, a
   run that posts a comment starting with the command prefix would trigger itself, forever.
3. **Only an explicit command becomes a run.** A human comment must open with the command
   prefix (default ``/ronin``); everything else is ``IGNORED`` with a reason, so a busy repo
   does not spawn a run on every comment.

What it cannot do offline, and says so: an ``issue_comment`` payload does not carry the
pull request's head SHA, so a command on a PR runs against the repository's default branch
unless a caller resolves the head ref via the API first. That is noted on the field rather
than guessed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: The comment prefix that marks a human command. Configurable per install.
COMMAND_PREFIX = "/ronin"

#: GitHub's webhook headers (lower-cased, as an ASGI/WSGI app usually presents them).
SIGNATURE_HEADER = "x-hub-signature-256"
EVENT_HEADER = "x-github-event"
DELIVERY_HEADER = "x-github-delivery"

_SIG_SCHEME = "sha256="


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time HMAC-SHA256 check of GitHub's ``X-Hub-Signature-256``.

    Returns ``False`` for every failure mode — no secret, no header, wrong scheme, wrong
    digest — rather than distinguishing them, because a caller must treat them all the same
    (reject) and a detailed reason is exactly what a signature check must not leak.
    """
    if not secret or not signature_header:
        return False
    if not signature_header.startswith(_SIG_SCHEME):
        return False
    presented = signature_header[len(_SIG_SCHEME) :]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(presented, expected)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """A run a webhook decided to start, reduced to what a worker needs to begin.

    Frozen and JSON-shaped so it can be enqueued into the durable :mod:`ronin_jobs` queue
    (or any queue) without this module depending on one.
    """

    repo: str
    """``owner/name`` of the repository the run acts on."""
    ref: str
    """Branch or SHA to check out. For an ``issue_comment`` this is the repo's default
    branch — the comment payload has no PR head SHA; a caller wanting the PR's head must
    resolve it via the API and override this."""
    prompt: str
    """The instruction for the agent — the comment text after the command prefix, or a
    synthesized instruction for a pull-request event."""
    actor: str
    """The GitHub login that triggered the run (for attribution and rate-limiting)."""
    event: str
    """The GitHub event name (``issue_comment`` / ``pull_request``)."""
    installation_id: int | None = None
    """The GitHub App installation this came through, when present."""
    delivery_id: str = ""
    """GitHub's ``X-Github-Delivery`` id — a natural idempotency key for the queue."""
    issue_number: int | None = None
    """The issue or PR number the command was posted on, when applicable."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "ref": self.ref,
            "prompt": self.prompt,
            "actor": self.actor,
            "event": self.event,
            "installation_id": self.installation_id,
            "delivery_id": self.delivery_id,
            "issue_number": self.issue_number,
        }


class Decision(StrEnum):
    ACCEPTED = "accepted"
    IGNORED = "ignored"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """What the handler decided, as a value. ``REJECTED`` is a security failure (bad
    signature / unparseable body); ``IGNORED`` is a valid webhook that should not start a
    run (a reason is always given); ``ACCEPTED`` carries the :class:`RunRequest`."""

    decision: Decision
    reason: str = ""
    run: RunRequest | None = None

    @property
    def accepted(self) -> bool:
        return self.decision is Decision.ACCEPTED

    @classmethod
    def accept(cls, run: RunRequest) -> WebhookOutcome:
        return cls(decision=Decision.ACCEPTED, reason="", run=run)

    @classmethod
    def ignore(cls, reason: str) -> WebhookOutcome:
        return cls(decision=Decision.IGNORED, reason=reason)

    @classmethod
    def reject(cls, reason: str) -> WebhookOutcome:
        return cls(decision=Decision.REJECTED, reason=reason)


def _is_bot(payload: Mapping[str, Any]) -> bool:
    sender = payload.get("sender")
    return isinstance(sender, Mapping) and sender.get("type") == "Bot"


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse_event(
    event: str,
    payload: Mapping[str, Any],
    *,
    command_prefix: str = COMMAND_PREFIX,
    delivery_id: str = "",
) -> WebhookOutcome:
    """Map a verified GitHub event to a decision. Pure — no signature check, no I/O.

    Handles ``issue_comment`` (a ``/prefix`` command) and ``pull_request`` (opened /
    reopened → a review run). Everything else, including ``ping``, is ``IGNORED`` with a
    reason so the caller can log why nothing happened.
    """
    repo_obj = payload.get("repository")
    repo = _str(repo_obj.get("full_name")) if isinstance(repo_obj, Mapping) else ""
    default_branch = _str(repo_obj.get("default_branch")) if isinstance(repo_obj, Mapping) else ""
    installation = payload.get("installation")
    installation_id = installation.get("id") if isinstance(installation, Mapping) else None
    if not isinstance(installation_id, int):
        installation_id = None

    if _is_bot(payload):
        return WebhookOutcome.ignore("sender is a bot (loop guard)")
    if not repo:
        return WebhookOutcome.ignore("payload carries no repository.full_name")

    if event == "issue_comment":
        if payload.get("action") != "created":
            return WebhookOutcome.ignore(f"issue_comment action {payload.get('action')!r}")
        comment = payload.get("comment")
        body = _str(comment.get("body")) if isinstance(comment, Mapping) else ""
        stripped = body.strip()
        if not stripped.startswith(command_prefix):
            return WebhookOutcome.ignore("comment is not a command")
        prompt = stripped[len(command_prefix) :].strip()
        if not prompt:
            return WebhookOutcome.ignore("command has no instruction after the prefix")
        issue = payload.get("issue")
        issue_number = issue.get("number") if isinstance(issue, Mapping) else None
        actor = _str(comment.get("user", {}).get("login")) if isinstance(comment, Mapping) else ""
        return WebhookOutcome.accept(
            RunRequest(
                repo=repo,
                ref=default_branch or "HEAD",
                prompt=prompt,
                actor=actor,
                event=event,
                installation_id=installation_id,
                delivery_id=delivery_id,
                issue_number=issue_number if isinstance(issue_number, int) else None,
            )
        )

    if event == "pull_request":
        if payload.get("action") not in {"opened", "reopened"}:
            return WebhookOutcome.ignore(f"pull_request action {payload.get('action')!r}")
        pr = payload.get("pull_request")
        if not isinstance(pr, Mapping):
            return WebhookOutcome.ignore("pull_request payload has no pull_request object")
        head = pr.get("head")
        ref = _str(head.get("sha")) if isinstance(head, Mapping) else ""
        number = pr.get("number")
        actor = _str(pr.get("user", {}).get("login")) if isinstance(pr, Mapping) else ""
        return WebhookOutcome.accept(
            RunRequest(
                repo=repo,
                ref=ref or default_branch or "HEAD",
                prompt="Review this pull request and report problems.",
                actor=actor,
                event=event,
                installation_id=installation_id,
                delivery_id=delivery_id,
                issue_number=number if isinstance(number, int) else None,
            )
        )

    return WebhookOutcome.ignore(f"event {event!r} is not one this app acts on")


def handle_webhook(
    *,
    secret: str,
    event: str,
    signature_header: str | None,
    body: bytes,
    enqueue: Callable[[RunRequest], None],
    command_prefix: str = COMMAND_PREFIX,
    delivery_id: str = "",
) -> WebhookOutcome:
    """Verify, parse, and (on ``ACCEPTED``) enqueue — the whole webhook path in one place.

    ``enqueue`` is the single seam to a queue: called exactly once, with the built
    :class:`RunRequest`, only when the outcome is ``ACCEPTED``. It is injected rather than
    imported so this module has no dependency on :mod:`ronin_jobs` and stays a pure unit in
    tests (pass ``list.append``). A rejection or an ignore never calls it.
    """
    if not verify_signature(secret, body, signature_header):
        return WebhookOutcome.reject("signature verification failed")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        return WebhookOutcome.reject(f"body is not valid JSON: {exc}")
    if not isinstance(payload, Mapping):
        return WebhookOutcome.reject("webhook body is not a JSON object")
    outcome = parse_event(event, payload, command_prefix=command_prefix, delivery_id=delivery_id)
    if outcome.accepted and outcome.run is not None:
        enqueue(outcome.run)
    return outcome


def main() -> int:  # pragma: no cover - module demo entry point
    """``python -m ronin_relay.github_app`` — the whole path, offline, with a fake queue."""
    secret = "demo-secret"

    def _signed(event: str, payload: dict[str, Any]) -> WebhookOutcome:
        body = json.dumps(payload).encode("utf-8")
        sig = _SIG_SCHEME + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return handle_webhook(
            secret=secret,
            event=event,
            signature_header=sig,
            body=body,
            enqueue=queue.append,
            delivery_id="demo-1",
        )

    queue: list[RunRequest] = []
    repo = {"full_name": "acme/app", "default_branch": "main"}
    print("ronin GitHub-App webhook — offline demo (no network, fake queue)\n")

    cases: list[tuple[str, str, dict[str, Any]]] = [
        (
            "human command",
            "issue_comment",
            {
                "action": "created",
                "repository": repo,
                "issue": {"number": 7},
                "comment": {"body": "/ronin fix the failing test", "user": {"login": "dev"}},
            },
        ),
        (
            "bot comment (loop guard)",
            "issue_comment",
            {
                "action": "created",
                "repository": repo,
                "sender": {"type": "Bot"},
                "comment": {"body": "/ronin do it again", "user": {"login": "ronin[bot]"}},
            },
        ),
        (
            "plain comment",
            "issue_comment",
            {
                "action": "created",
                "repository": repo,
                "comment": {"body": "thanks!", "user": {"login": "dev"}},
            },
        ),
        (
            "PR opened",
            "pull_request",
            {
                "action": "opened",
                "repository": repo,
                "pull_request": {"number": 12, "head": {"sha": "abc123"}, "user": {"login": "dev"}},
            },
        ),
    ]
    for label, event, payload in cases:
        outcome = _signed(event, payload)
        detail = outcome.reason or (outcome.run.prompt if outcome.run else "")
        print(f"  {label:<28} {outcome.decision.value:<9} {detail}")

    # A tampered body must be rejected and must not enqueue.
    good = json.dumps({"action": "created", "repository": repo}).encode()
    bad = handle_webhook(
        secret=secret,
        event="issue_comment",
        signature_header=_SIG_SCHEME + "0" * 64,
        body=good,
        enqueue=queue.append,
    )
    print(f"  {'forged signature':<28} {bad.decision.value:<9} {bad.reason}")

    print(f"\nenqueued {len(queue)} run(s): {[r.prompt for r in queue]}")
    # Two runs are enqueued: the human command and the opened PR. The bot comment, the
    # plain comment, and the forged-signature request each enqueued nothing.
    ok = len(queue) == 2 and all(r.repo == "acme/app" for r in queue)
    print("only the two actionable events enqueued a run:", ok)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
