"""Run `ronin.safety.demo`. Of the demos, this is the one whose claims are load-bearing.

`docs/STACK.md` measured eleven `demo.py` modules at 0%; `core`, `tools` and `providers`
have since been covered by tests that execute them, and `context` was already covered.
This is the fifth, and the layer where a stale demo does the most damage: the approval gate
is the feature people decide whether to trust, and a demo that *claims* a refusal it no
longer performs is worse than no demo, because it manufactures confidence.

So these tests assert the refusals, not merely that it exits 0. Each of the six scenes
makes a specific promise:

1. every effective rule names the file it came from — provenance, so a permission can be revoked
2. `echo safe; rm -rf /` cannot hide behind the harmless first segment
3. a refusal carries a reason the model can act on ("use staging instead")
4. "remember this" is a request, never a self-granted authorization
5. injected text is flagged rather than obeyed, and the follow-up call escalates
6. the cloud metadata endpoint is refused in every notation, and a URL is redacted
7. the sandbox reports what *this* machine can actually enforce
"""

from __future__ import annotations

import pytest

from ronin.safety import demo


async def test_the_demo_runs_offline_and_reaches_every_scene(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await demo.main() == 0
    out = capsys.readouterr().out
    for heading in (
        "Layered config",
        "Per-segment analysis",
        "use staging instead",
        "Remember is a request",
        "Injection: flagged, not obeyed",
        "Where a fetch may go",
        "Sandbox: honest about this machine",
    ):
        assert heading in out, f"scene missing: {heading}"


async def test_the_demo_shows_a_denial_and_not_only_approvals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gate's whole value is the calls it stops. A demo of an approval gate that
    only ever shows approvals is demonstrating the half that cannot go wrong."""
    await demo.main()
    out = capsys.readouterr().out.lower()
    assert "deny" in out or "denied" in out or "refus" in out, "no refusal was rendered"


async def test_the_segment_scene_surfaces_the_hidden_destructive_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scene 2 is the parser's reason to exist, shown rather than described.

    `echo safe; rm -rf /` must be analysed as two segments. A gate that reads only the
    first binary approves this, which is exactly the bug the per-segment analysis
    exists to prevent — so the demo has to be seen doing it.
    """
    await demo.main()
    out = capsys.readouterr().out
    assert "rm -rf /" in out
    assert "echo safe" in out


async def test_the_injection_scene_flags_rather_than_obeys(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Flagged *and* escalating: a taint that does not raise the bar on the next call
    is a warning label, not a control."""
    await demo.main()
    out = capsys.readouterr().out
    assert "Injection: flagged, not obeyed" in out
    assert "escalate" in out.lower()


async def test_the_url_scene_refuses_every_address_it_lists(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The scene prints one line per URL, and the word on that line is the claim.

    `LEAKED` is what it prints when `check_url` lets something through, so asserting
    its absence is asserting the scene is telling the truth rather than merely running
    — the same reason `tests/cli/test_ronin_cli_demo.py` looks for `FAILED:` instead of
    trusting the exit code.
    """
    await demo.main()
    out = capsys.readouterr().out
    assert "LEAKED" not in out, "the demo printed a URL its own policy failed to block"
    scene = out[out.index("Where a fetch may go") :]
    for address in ("169.254.169.254", "2130706433", "metadata.google.internal", "100.64.0.1"):
        assert address in scene, f"the scene stopped covering {address}"
    assert "<redacted>" in scene, "the redaction half of the scene is missing"
    # The resolved check is the half a literal check cannot do, so the scene has to show
    # a name that looks fine being refused for where it points.
    #
    # The host is split out of the line and compared with `==`, rather than matched with
    # `in`/`endswith` against the whole line. Two reasons, and they agree. It is a
    # stronger assertion: it rules out the demo printing this name as *allowed*, which is
    # the one outcome the test exists to catch, and it cannot pass on a longer name that
    # merely ends the same way. And it is the shape CodeQL's "incomplete URL substring
    # sanitization" rule asks for — that rule fires on `in` *and* on `endswith`, because
    # `endswith("example.com")` also matches `evil-example.com`; here the string is demo
    # output rather than a URL being authorized, but parse-then-compare is the right
    # habit either way.
    blocked = {
        line.split()[-1] for line in scene.splitlines() if line.strip().startswith("blocked")
    }
    assert "harmless-looking.example.com" in blocked, (
        f"the public-name-pointing-inward case is not shown as blocked; saw {sorted(blocked)}"
    )
    assert "and that is what gets dialled" in scene, "pinning is the point, so it is shown"
    # The *redacted* lines only — the ones the scene prints as `→ <result>`. Splitting on
    # the first arrow in the scene used to work and no longer does, because the resolution
    # block above prints arrows too; the un-redacted "before" line legitimately contains
    # the secret, so a looser match would fail on the scene telling the truth.
    redacted = [line for line in scene.splitlines() if line.strip().startswith("→")]
    assert redacted, "the redaction examples print one `→ result` line each"
    assert all("live_9f3a" not in line for line in redacted), "a secret survived redaction"
    assert all("ya29." not in line for line in redacted), "an OAuth fragment survived"


async def test_the_sandbox_scene_reports_this_machine_rather_than_an_ideal_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The heading promises honesty about the host, so the scene must not print a
    capability list that is the same everywhere — a sandbox that claims enforcement it
    does not have is the most dangerous sentence in this layer."""
    await demo.main()
    assert "Sandbox: honest about this machine" in capsys.readouterr().out
