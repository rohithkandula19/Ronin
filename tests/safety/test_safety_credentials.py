"""``ronin.safety.credentials`` — find the key, do not print the key.

Two properties carry the module and both are tested as properties rather than as
examples, because both are the kind that a plausible-looking refactor breaks:

* **No hint discloses its match.** Checked against every pattern in :data:`PATTERNS`
  via a generated sample per kind, so adding a pattern without thinking about masking
  fails here rather than in somebody's CI log.
* **A false negative is the failure that matters.** Suppression is narrow on purpose,
  and the tests pin the *narrowness* — a key full of x's is still a key.

The v1 module these came from documented a safety assertion it did not have
(``if match in hint``, which cannot fire because a hint contains an ellipsis and a
match does not). :func:`test_the_v1_safety_gate_could_not_fire` is that bug, kept as a
test so the replacement is not quietly reverted to the version that reads the same.

Offline by construction: every input is a string.
"""

from __future__ import annotations

import re

import pytest

from ronin.safety.credentials import (
    ALLOW_PRAGMAS,
    ELISION_RATIO,
    PATTERNS,
    PLACEHOLDER_MARKERS,
    REVEALED,
    Finding,
    find_secrets,
    find_secrets_in_diff,
    mask,
    revealing,
    suppressed,
)


def key(prefix: str, body: str) -> str:
    """A sample credential, assembled at run time and never written as one token.

    This matters more than it looks. Every value below is synthetic, but a
    credential scanner cannot know that — and a file full of key-shaped literals
    trips this repository's *own* `ronin scan`, GitHub's secret scanning, and
    whatever else an operator points at the tree. Each hit is a false positive
    somebody has to triage, forever, and a security tool people learn to ignore is
    a security tool that has stopped working.

    Splitting each value at a point the pattern needs to be contiguous means the
    key exists only in memory. `\bAKIA[0-9A-Z]{16}\b` cannot match `"AKIA", "Q7…"`
    because what follows `AKIA` in the source is a quote.

    The allow-pragma would also have worked and is deliberately not used: a
    pragma asks every reader to trust an annotation, while a value that never
    exists in the file needs no trust.
    """
    return prefix + body


#: One realistic value per pattern kind. Written out rather than generated from the
#: regexes, because a sample derived from the pattern would pass by construction and
#: prove only that the derivation works.
SAMPLES: dict[str, str] = {
    "anthropic-key": key("sk-ant-", "api03-q7Fw2nR8xLm4vTgH1Zb6"),
    "openai-key": key("sk-proj-", "a" * 40),
    "stripe-live-sk": key("sk_live_", "51HxQpLmNbVcXzAsDfGh"),
    "stripe-test-sk": key("sk_test_", "51HxQpLmNbVcXzAsDfGh"),
    "stripe-rk": key("rk_live_", "51HxQpLmNbVcXzAsDfGh"),
    "stripe-pk": key("pk_live_", "51HxQpLmNbVcXzAsDfGh"),
    "github-pat": key("ghp_", "g5Kd8Wq2LzNb7XcVaTeRyUiOpMnBhG"),
    "github-oauth": key("gho_", "g5Kd8Wq2LzNb7XcVaTeR"),
    "slack-bot": key("xoxb-2154-8891-", "kQw7ZrTn3LpXvBmCdEfG"),
    "slack-user": key("xoxp-2154-8891-4471-", "kQw7ZrTn3LpXvBmCdEfG"),
    "slack-app": key("xapp-1-A04KQ-8891-", "kQw7ZrTn3LpXvBmCdEfG"),
    "aws-akid": key("AKIA", "Q7RWZP2MLN4KXTBV"),
    "linear-key": key("lin_api_", "8WqZr3TnLpXvBmCdEfGh"),
    "jwt": key("eyJhbGciOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K"),
    "fernet": key("gAAAAA", "B" * 50),
    "notion-secret": key("secret_", "c" * 40),
    "resend-key": key("re_", "8WqZr3TnLpXvBmCdEfGh"),
    "private-key": key("-----BEGIN RSA ", "PRIVATE KEY-----"),
}

#: The one sample spelled out more than once below, so it is named once here.
AWS = SAMPLES["aws-akid"]


def test_every_pattern_has_a_sample() -> None:
    """The suite below is only exhaustive if this is. A new pattern lands here first."""
    assert set(SAMPLES) == {kind for kind, _ in PATTERNS}


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_every_pattern_matches_its_own_sample(kind: str) -> None:
    found = find_secrets(f"TOKEN = {SAMPLES[kind]!r}\n", "config.py")
    assert [finding.kind for finding in found] == [kind]


# --------------------------------------------------------------------------- #
# the property the whole module is for
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_no_finding_ever_carries_its_secret(kind: str) -> None:
    """The contract, over the real pattern set: a report is a location, not a copy.

    The consequence of getting this wrong is not an incorrect report — it is the
    secret written into a terminal scrollback, a CI log, and whatever quotes the CI
    log. So it is asserted three ways: the hint is not the value, does not contain the
    value, and does not show more than half of it.
    """
    secret = SAMPLES[kind]
    (finding,) = find_secrets(f"key={secret}\n", "config.py")
    assert finding.hint != secret
    assert secret not in finding.hint
    assert not revealing(finding.hint, secret)


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_a_hint_shows_no_more_than_four_characters_from_each_end(kind: str) -> None:
    secret = SAMPLES[kind]
    hint = mask(kind, secret)
    if hint == kind:  # too short to sample — the safe answer
        return
    head, _, tail = hint.partition("…")
    assert len(head) == REVEALED and len(tail) == REVEALED
    assert secret.startswith(head) and secret.endswith(tail)


def test_a_short_match_is_named_rather_than_sampled() -> None:
    """Under ``2 * REVEALED * ELISION_RATIO`` characters, eight of them would be most
    of it — so the kind is the whole answer."""
    short = "A" * (2 * REVEALED * ELISION_RATIO - 1)
    assert mask("aws-akid", short) == "aws-akid"
    assert mask("aws-akid", "A" * (2 * REVEALED * ELISION_RATIO)) != "aws-akid"


def test_a_hint_that_is_the_secret_is_caught_by_the_first_clause() -> None:
    """The obvious failure, and the one `revealing`'s character count would miss:
    equality and containment are checked before any ratio, because a hint that *is*
    the key has no fraction elided to measure."""
    secret = AWS
    assert revealing(secret, secret)
    assert revealing(f"aws-akid: {secret}", secret)


def test_the_v1_safety_gate_could_not_fire() -> None:
    """Why :func:`revealing` exists, stated as the bug it replaces.

    ``ronin_cli.secret_scan`` claimed "the raw matched value is asserted absent from
    every emitted hint before returning" and implemented it as ``if match in hint``.
    A hint always contains ``…`` and a match never does, so that condition is false
    for every input the function can produce — including for a hint that is almost all
    of the key. The check was decorative, and this pins that the replacement is not.
    """
    secret = AWS
    leaky = secret[:16] + "…" + secret[-2:]

    assert secret not in leaky, "the v1 condition"  # v1 would have let this through
    assert revealing(leaky, secret), "the replacement catches it"


# --------------------------------------------------------------------------- #
# suppression: narrow on purpose
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("marker", PLACEHOLDER_MARKERS)
def test_a_documentation_value_is_not_reported(marker: str) -> None:
    text = f"ANTHROPIC_API_KEY=sk-ant-api03-{marker}Zq7Fw2nR8xLm4vTgH1Zb6\n"
    assert find_secrets(text, "README.md") == []


@pytest.mark.parametrize("pragma", ALLOW_PRAGMAS)
def test_a_line_pragma_silences_that_line(pragma: str) -> None:
    live = SAMPLES["github-pat"]
    assert find_secrets(f"token = {live!r}  # {pragma}\n", "t.py") == []
    assert find_secrets(f"token = {live!r}\n", "t.py") != []


def test_a_run_of_x_is_still_a_key() -> None:
    """The false negative the suppression list is deliberately too narrow to cause.

    A commit guard's marker list also carries bare repetitions — ``XXXXXX``,
    ``1234567890``, ``DEADBEEF`` — and a real high-entropy key can contain any of
    them. Suppressing on those trades a false positive for a missed key, which is the
    wrong side of that trade for a scanner whose whole job is not to miss one.
    """
    assert find_secrets("ghp_" + "x" * 36 + "\n", "t.py") != []
    assert find_secrets("ghp_" + "1234567890" * 3 + "abcdef\n", "t.py") != []


def test_suppression_reads_the_line_not_the_file() -> None:
    """A pragma protects its own line and nothing after it."""
    live = SAMPLES["github-pat"]
    text = f"a = {live!r}  # {ALLOW_PRAGMAS[0]}\nb = {live!r}\n"
    assert [finding.line for finding in find_secrets(text, "t.py")] == [2]


def test_suppressed_is_case_insensitive_on_both_halves() -> None:
    assert suppressed("sk-ant-api03-ExAmPlEZq7Fw2nR8xLm4", "x = 1")
    assert suppressed("ghp_realish", "token  # RONIN:ALLOW-SECRET")


# --------------------------------------------------------------------------- #
# the shape of a report
# --------------------------------------------------------------------------- #


def test_findings_are_sorted_and_carry_their_path() -> None:
    text = f"\n\n{SAMPLES['aws-akid']}\n{SAMPLES['github-pat']}\n"
    found = find_secrets(text, "deploy/vars.tf")
    assert [(f.line, f.kind) for f in found] == [(3, "aws-akid"), (4, "github-pat")]
    assert {f.path for f in found} == {"deploy/vars.tf"}


def test_one_value_matching_two_patterns_is_reported_once() -> None:
    """``sk-ant-…`` also satisfies the looser ``sk-`` OpenAI pattern.

    Reported twice it reads as two leaked keys, and the person reading the report
    counts wrong in the direction that wastes their time on the second one.
    """
    found = find_secrets(SAMPLES["anthropic-key"] + "\n", "t.py")
    assert [f.kind for f in found] == ["anthropic-key"]


def test_empty_text_is_not_a_special_case_anybody_has_to_remember() -> None:
    assert find_secrets("", "t.py") == []


def test_a_finding_is_comparable_so_tests_can_say_what_they_expect() -> None:
    """The whole record, spelled out once — including what a real hint looks like.

    An AWS access key id is the shortest thing the pattern set matches: twenty
    characters, of which the leading ``AKIA`` is fixed. So the hint discloses four of
    its sixteen variable characters, which is the deliberate floor of the masking rule
    rather than an accident of this example — and it is what AWS's own console shows
    to identify a key.
    """
    (found,) = find_secrets(AWS + "\n", "t.py")
    assert found == Finding(path="t.py", line=1, kind="aws-akid", hint="AKIA…XTBV")


# --------------------------------------------------------------------------- #
# git history
# --------------------------------------------------------------------------- #

DIFF = f"""commit 4f2c1ab9d3e5b7a8c0d1e2f3a4b5c6d7e8f9a0b1
Author: Someone <s@example.com>

    add config

diff --git a/app/settings.py b/app/settings.py
--- /dev/null
+++ b/app/settings.py
@@ -0,0 +1,3 @@
+DEBUG = True
+TOKEN = "{SAMPLES["github-pat"]}"
+NAME = "app"
commit 9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b
Author: Someone <s@example.com>

    remove it

diff --git a/app/settings.py b/app/settings.py
--- a/app/settings.py
+++ b/app/settings.py
@@ -1,3 +1,3 @@
 DEBUG = True
-TOKEN = "{SAMPLES["github-pat"]}"
+TOKEN = os.environ["TOKEN"]
 NAME = "app"
"""


def test_a_key_deleted_by_a_later_commit_is_still_found() -> None:
    """The reason ``--history`` exists at all.

    Committing the deletion does not remove the blob — ``git show`` prints it back and
    so does anybody who cloned. A working-tree scan of this repository is clean and
    the repository is still leaking.
    """
    found = find_secrets_in_diff(DIFF)
    assert [f.kind for f in found] == ["github-pat"]
    assert found[0].commit == "4f2c1ab9d3e5b7a8c0d1e2f3a4b5c6d7e8f9a0b1"
    assert found[0].path == "app/settings.py"


def test_a_history_finding_names_the_line_in_the_commit_that_added_it() -> None:
    """Line 2 of that hunk, counted the way the commit counts it — which is what makes
    the location usable with ``git show <commit>:<path>``."""
    (found,) = find_secrets_in_diff(DIFF)
    assert found.line == 2


def test_a_removed_line_does_not_advance_the_new_file_counter() -> None:
    """The off-by-one this parser is most likely to acquire.

    A ``-`` line exists in the pre-image only. Counting it would push every later
    finding in the same hunk down by one, and a line number that is nearly right is
    worse than none: it sends the reader to a line that looks innocent.
    """
    secret = SAMPLES["aws-akid"]
    diff = (
        "commit 1111111111111111111111111111111111111111\n"
        "+++ b/x.tf\n"
        "@@ -1,4 +1,3 @@\n"
        " one\n"
        "-two\n"
        "-three\n"
        f"+{secret}\n"
    )
    (found,) = find_secrets_in_diff(diff)
    assert found.line == 2


def test_history_findings_mask_exactly_as_working_tree_findings_do() -> None:
    """One masking path, not two — the v1 renderer had a second loop that had drifted."""
    (history,) = find_secrets_in_diff(DIFF)
    (tree,) = find_secrets(SAMPLES["github-pat"], "app/settings.py")
    assert history.hint == tree.hint


def test_a_diff_with_no_hunk_header_reports_nothing_rather_than_line_zero() -> None:
    """Malformed input is common — a truncated log, a pager artefact. It must not
    produce findings pointing at line 0 of a file."""
    assert find_secrets_in_diff(f"+++ b/x.py\n+{SAMPLES['github-pat']}\n") == []


def test_a_deleted_file_post_image_is_not_a_path() -> None:
    diff = (
        "commit 2222222222222222222222222222222222222222\n"
        "--- a/x.py\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        f"-{SAMPLES['github-pat']}\n"
    )
    assert find_secrets_in_diff(diff) == []


def test_patterns_all_compile_and_none_matches_the_empty_string() -> None:
    """A pattern that matches nothing at all would flag every file in the tree."""
    for kind, pattern in PATTERNS:
        compiled = re.compile(pattern)
        assert compiled.search("") is None, kind


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
