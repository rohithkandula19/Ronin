"""The GitHub-App webhook path: a bad signature never runs, a bot never runs, only a command runs.

Pure and offline — every test builds a payload, signs it with a known secret, and asserts
the decision and whether the injected ``enqueue`` fired. No network, no queue, no agent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from ronin_relay.github_app import (
    Decision,
    RunRequest,
    handle_webhook,
    parse_event,
    verify_signature,
)

SECRET = "s3cr3t"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _deliver(event: str, payload: dict[str, Any], *, secret: str = SECRET) -> tuple:
    body = json.dumps(payload).encode("utf-8")
    queue: list[RunRequest] = []
    outcome = handle_webhook(
        secret=secret,
        event=event,
        signature_header=_sign(body),
        body=body,
        enqueue=queue.append,
        delivery_id="d-1",
    )
    return outcome, queue


_REPO = {"full_name": "acme/app", "default_branch": "main"}


# --------------------------------------------------------------------------- #
# signature
# --------------------------------------------------------------------------- #


def test_a_correct_signature_verifies() -> None:
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, _sign(body)) is True


def test_a_wrong_secret_does_not_verify() -> None:
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, _sign(body, secret="other")) is False


def test_missing_or_malformed_signature_headers_do_not_verify() -> None:
    body = b"{}"
    assert verify_signature(SECRET, body, None) is False
    assert verify_signature(SECRET, body, "garbage") is False
    assert verify_signature(SECRET, body, "sha1=deadbeef") is False
    assert verify_signature("", body, _sign(body)) is False


def test_a_forged_signature_is_rejected_and_never_enqueues() -> None:
    body = json.dumps({"action": "created", "repository": _REPO}).encode()
    queue: list[RunRequest] = []
    outcome = handle_webhook(
        secret=SECRET,
        event="issue_comment",
        signature_header="sha256=" + "0" * 64,
        body=body,
        enqueue=queue.append,
    )
    assert outcome.decision is Decision.REJECTED
    assert queue == []


def test_a_non_json_body_is_rejected() -> None:
    queue: list[RunRequest] = []
    body = b"not json"
    outcome = handle_webhook(
        secret=SECRET,
        event="issue_comment",
        signature_header=_sign(body),
        body=body,
        enqueue=queue.append,
    )
    assert outcome.decision is Decision.REJECTED
    assert queue == []


# --------------------------------------------------------------------------- #
# issue_comment
# --------------------------------------------------------------------------- #


def test_a_command_comment_becomes_a_run() -> None:
    outcome, queue = _deliver(
        "issue_comment",
        {
            "action": "created",
            "repository": _REPO,
            "issue": {"number": 7},
            "comment": {"body": "/ronin fix the flaky test", "user": {"login": "dev"}},
            "installation": {"id": 42},
        },
    )
    assert outcome.decision is Decision.ACCEPTED
    assert len(queue) == 1
    run = queue[0]
    assert run.repo == "acme/app"
    assert run.ref == "main"  # default branch: the comment payload has no PR head
    assert run.prompt == "fix the flaky test"
    assert run.actor == "dev"
    assert run.installation_id == 42
    assert run.issue_number == 7
    assert run.delivery_id == "d-1"


def test_a_bot_comment_is_ignored_to_prevent_a_loop() -> None:
    outcome, queue = _deliver(
        "issue_comment",
        {
            "action": "created",
            "repository": _REPO,
            "sender": {"type": "Bot"},
            "comment": {"body": "/ronin do it again", "user": {"login": "ronin[bot]"}},
        },
    )
    assert outcome.decision is Decision.IGNORED
    assert "bot" in outcome.reason
    assert queue == []


def test_a_plain_comment_is_ignored() -> None:
    outcome, queue = _deliver(
        "issue_comment",
        {
            "action": "created",
            "repository": _REPO,
            "comment": {"body": "looks good to me", "user": {"login": "dev"}},
        },
    )
    assert outcome.decision is Decision.IGNORED
    assert queue == []


def test_a_command_with_no_instruction_is_ignored() -> None:
    outcome, queue = _deliver(
        "issue_comment",
        {
            "action": "created",
            "repository": _REPO,
            "comment": {"body": "/ronin   ", "user": {"login": "dev"}},
        },
    )
    assert outcome.decision is Decision.IGNORED
    assert queue == []


def test_an_edited_comment_is_ignored() -> None:
    outcome, queue = _deliver(
        "issue_comment",
        {
            "action": "edited",
            "repository": _REPO,
            "comment": {"body": "/ronin go", "user": {"login": "dev"}},
        },
    )
    assert outcome.decision is Decision.IGNORED
    assert queue == []


# --------------------------------------------------------------------------- #
# pull_request
# --------------------------------------------------------------------------- #


def test_an_opened_pull_request_becomes_a_review_run_against_the_head_sha() -> None:
    outcome, queue = _deliver(
        "pull_request",
        {
            "action": "opened",
            "repository": _REPO,
            "pull_request": {"number": 12, "head": {"sha": "abc123"}, "user": {"login": "dev"}},
        },
    )
    assert outcome.decision is Decision.ACCEPTED
    assert queue[0].ref == "abc123"
    assert queue[0].issue_number == 12
    assert "review" in queue[0].prompt.lower()


def test_a_synchronize_pull_request_is_ignored() -> None:
    outcome, _ = _deliver(
        "pull_request",
        {"action": "synchronize", "repository": _REPO, "pull_request": {"number": 1}},
    )
    assert outcome.decision is Decision.IGNORED


# --------------------------------------------------------------------------- #
# other events / defensive parsing
# --------------------------------------------------------------------------- #


def test_a_ping_is_ignored() -> None:
    outcome, queue = _deliver("ping", {"zen": "Keep it simple", "repository": _REPO})
    assert outcome.decision is Decision.IGNORED
    assert queue == []


def test_a_payload_with_no_repository_is_ignored_not_crashed() -> None:
    # parse_event is defensive: a malformed payload is a decision, never a KeyError.
    outcome = parse_event("issue_comment", {"action": "created"})
    assert outcome.decision is Decision.IGNORED


def test_a_custom_command_prefix_is_honoured() -> None:
    body = json.dumps(
        {
            "action": "created",
            "repository": _REPO,
            "comment": {"body": "@bot ship it", "user": {"login": "dev"}},
        }
    ).encode()
    queue: list[RunRequest] = []
    outcome = handle_webhook(
        secret=SECRET,
        event="issue_comment",
        signature_header=_sign(body),
        body=body,
        enqueue=queue.append,
        command_prefix="@bot",
    )
    assert outcome.decision is Decision.ACCEPTED
    assert queue[0].prompt == "ship it"


def test_the_run_request_is_json_shaped_for_a_queue() -> None:
    run = RunRequest(repo="a/b", ref="main", prompt="go", actor="dev", event="issue_comment")
    assert json.loads(json.dumps(run.as_dict()))["repo"] == "a/b"
