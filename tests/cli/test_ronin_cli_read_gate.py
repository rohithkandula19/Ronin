"""Deny rules that name a read tool, and whether anything actually consults them.

The rules parsed, matched, and resolved to DENY. `PolicyEngine.evaluate` returned
DENY. And the file was read anyway, because the engine was never asked: the loop
gated the *consult* on ``spec.requires_approval``, and ``read``, ``grep``, ``glob``
and ``ls`` all inherit ``False``. Every layer worked except the one that connects
them, which is the failure mode a unit test cannot see — each piece passes its own.

So these tests run the **real** loop over the **real** registry with settings loaded
from a **real** file on disk, and assert on what the model gets back. Two promises
are at stake and both were broken:

* a rule a user writes in ``settings.json`` denies the read it names, and
* the unconditional deny list refuses key material, which ``docs/SUBSYSTEMS.md``
  states as "`.env` writes denied and reads allowed; key material denied both ways".

Offline: a scripted provider, a tmp_path workspace, no shell.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import stream_harness as h

from ronin.cli.sdk import Agent
from ronin.core.types import Mode, ToolEnd
from ronin.safety.settings import PROJECT_SETTINGS

SECRET = "AWS_SECRET_ACCESS_KEY=hunter2"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----"


def workspace(root: Path, rules: list[dict[str, Any]] | None = None) -> Path:
    (root / ".ronin").mkdir(parents=True, exist_ok=True)
    (root / PROJECT_SETTINGS).write_text(json.dumps({"rules": rules or []}), encoding="utf-8")
    (root / ".env").write_text(SECRET + "\n", encoding="utf-8")
    (root / "certs").mkdir(exist_ok=True)
    (root / "certs" / "tls.key").write_text(PRIVATE_KEY + "\nzzz\n", encoding="utf-8")
    (root / "README.md").write_text("ordinary prose\n", encoding="utf-8")
    return root


async def read_through_the_loop(root: Path, path: str, *, mode: Mode = Mode.FULL) -> ToolEnd:
    """One turn whose only act is reading ``path``. Returns its ``ToolEnd``."""
    router, _provider = h.scripted_router(
        [h.provider_calls("read", {"path": path}), h.provider_says("done")]
    )
    agent = await Agent.open(
        root,
        router=router,
        mode=mode,
        home=root / "home",
        environ={},
        record=False,
        connect_mcp=False,
    )
    try:
        ends = [event async for event in agent.stream("read it", verify=False)]
    finally:
        await agent.aclose()
    ended = [event for event in ends if isinstance(event, ToolEnd)]
    assert ended, "the scripted turn produced no tool call"
    return ended[0]


DENY_ENV = [{"tool": "read", "decision": "deny", "path": "**/.env", "reason": "secrets stay out"}]


# --------------------------------------------------------------------------- #
# a rule the user wrote
# --------------------------------------------------------------------------- #


async def test_a_deny_rule_on_read_actually_denies_the_read(tmp_path: Path) -> None:
    end = await read_through_the_loop(workspace(tmp_path, DENY_ENV), ".env")
    assert end.result.ok is False
    assert SECRET not in (end.result.content or "")
    assert "secrets stay out" in (end.result.error or "")


async def test_the_rule_does_not_deny_every_read(tmp_path: Path) -> None:
    """The control. A deny that blocked everything would pass the test above for free."""
    end = await read_through_the_loop(workspace(tmp_path, DENY_ENV), "README.md")
    assert end.result.ok is True
    assert "ordinary prose" in end.result.content


async def test_a_wildcard_deny_covers_the_read_family_too(tmp_path: Path) -> None:
    rules = [{"tool": "*", "decision": "deny", "reason": "read-only audit, nothing at all"}]
    end = await read_through_the_loop(workspace(tmp_path, rules), "README.md")
    assert end.result.ok is False
    assert "nothing at all" in (end.result.error or "")


@pytest.mark.parametrize("mode", [Mode.ASK, Mode.AUTO_EDIT, Mode.FULL])
async def test_no_mode_waives_a_read_deny_rule(tmp_path: Path, mode: Mode) -> None:
    """`--yolo`-shaped modes waive the *prompt*, never a rule that says deny."""
    end = await read_through_the_loop(workspace(tmp_path, DENY_ENV), ".env", mode=mode)
    assert end.result.ok is False
    assert SECRET not in (end.result.content or "")


# --------------------------------------------------------------------------- #
# the unconditional list, which no settings file has to ask for
# --------------------------------------------------------------------------- #


async def test_key_material_is_refused_on_the_way_in(tmp_path: Path) -> None:
    """docs/SUBSYSTEMS.md: "key material denied both ways". It was denied neither."""
    end = await read_through_the_loop(workspace(tmp_path), "certs/tls.key")
    assert end.result.ok is False
    assert PRIVATE_KEY not in (end.result.content or "")
    assert "key_material_read" in (end.result.error or "")


async def test_an_env_file_is_readable_without_a_rule_saying_otherwise(tmp_path: Path) -> None:
    """The other half of the documented split: `.env` *writes* are denied, reads are not.

    Pinned because the tempting over-fix for the bug above is to deny everything that
    looks sensitive, and that would break the ordinary case of asking the agent why a
    config value is not being picked up.
    """
    end = await read_through_the_loop(workspace(tmp_path), ".env")
    assert end.result.ok is True
    assert SECRET in end.result.content


# --------------------------------------------------------------------------- #
# nobody is prompted for a file read
# --------------------------------------------------------------------------- #


async def test_an_allowed_read_asks_no_human(tmp_path: Path) -> None:
    """Consulting the engine must not turn every read into a prompt.

    `PolicyEngine` relaxes an unconfigured read-only tool to ALLOW without an asker.
    If that stopped being true, an unattended session would refuse its own file reads
    — which is how a fix for this becomes worse than the bug.
    """
    root = workspace(tmp_path)
    router, _provider = h.scripted_router(
        [h.provider_calls("read", {"path": "README.md"}), h.provider_says("done")]
    )
    # No asker: an unattended session refuses anything that needs a human.
    agent = await Agent.open(
        root,
        router=router,
        mode=Mode.ASK,
        home=root / "home",
        environ={},
        record=False,
        connect_mcp=False,
        asker=None,
    )
    try:
        ends = [event async for event in agent.stream("read it", verify=False)]
    finally:
        await agent.aclose()
    end = next(event for event in ends if isinstance(event, ToolEnd))
    assert end.result.ok is True, "an ordinary read must not need a human who is not there"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
