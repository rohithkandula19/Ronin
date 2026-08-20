"""Run `ronin.agents.demo` — the layer whose demo makes the strongest claims.

At 0% this was the last demo module in the tree unexecuted by any test, and it is the
one where a stale demo misleads most: every section asserts a *containment* property.
"plan mode cannot write", "test-writer cannot touch src/", "a hook's exit 2 blocks the
call" — a demo that printed those while the enforcement had rotted would be actively
worse than no demo, because it manufactures exactly the confidence a person uses to
decide whether to let an agent near their repo.

So these tests assert the refusals, not the exit code. Two of them re-derive the
guarantee from the printed output rather than trusting the narration: that `write` is
absent from the plan-mode tool list, and that the blocked write left the file on disk
unchanged.
"""

from __future__ import annotations

import pytest

from ronin.agents import demo


async def test_the_demo_runs_offline_and_reaches_every_section(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await demo.main() == 0
    out = capsys.readouterr().out
    for heading in (
        "1. plan mode: the registry does not contain write",
        "2. the plan is parsed from prose, approved, and pinned as the objective",
        "3. a subagent loaded from .ronin/agents/doc-writer.md on disk",
        "4. test-writer cannot touch src/ — enforced by the tool, not the prompt",
        "5. a PreToolUse hook blocks with exit 2, and its reason reaches the model",
        "what this proved",
    ):
        assert heading in out, f"section missing: {heading}"


async def test_plan_mode_is_shown_without_write_not_merely_described(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The claim is in the heading, so the printed tool list has to back it.

    Read out of the output rather than asserted against the registry directly: the
    point is that what a person *sees* is true, which is a different property from
    the code being right.
    """
    await demo.main()
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "plan mode tools" in ln)
    for mutating in ("'write'", "'edit'", "'multi_edit'"):
        assert mutating not in line, f"plan mode listed {mutating}: {line}"
    assert "'read'" in line, f"plan mode listed no read tool: {line}"


async def test_a_subagent_is_loaded_from_a_real_file_on_disk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Section 3 is the `.ronin/agents/*.md` path end to end — frontmatter parsed
    into something the existing TaskTool accepts, with its source named."""
    await demo.main()
    out = capsys.readouterr().out
    assert "doc-writer" in out
    assert "doc-writer.md" in out
    assert "catalogue handed to TaskTool" in out


async def test_the_blocked_write_is_shown_refused_and_not_merely_warned(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Section 4 is the one that would be worst to get wrong.

    `test-writer` may write tests and nothing else. If the demo showed the refusal
    while the wrapper had stopped refusing, the printed line would be a promise the
    code no longer keeps.
    """
    await demo.main()
    out = capsys.readouterr().out
    assert "test-writer tools" in out
    assert "write_paths" in out
    assert "src/app.py" in out, "the refused path should be named"
    lowered = out.lower()
    assert "refus" in lowered or "not ok" in lowered or "✗" in out


async def test_the_hook_refusal_reaches_the_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """exit 2 blocks and its stderr becomes the model's error; exit 1 only warns.
    Both halves are claimed in the closing summary, so both should appear."""
    await demo.main()
    out = capsys.readouterr().out
    assert "exit 2" in out
    assert "exit 1 warns" in out


async def test_the_closing_summary_claims_only_what_the_sections_showed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The summary is the part a reader remembers, so it is worth pinning verbatim —
    if a section is removed, the claim must go with it."""
    await demo.main()
    out = capsys.readouterr().out
    for claim in (
        "plan mode is a registry without write",
        "a .md file on disk becomes a SubagentType the existing TaskTool accepts",
        "test-writer's lane is enforced by a wrapper that refuses, with a reason",
    ):
        assert claim in out, f"summary claim missing: {claim}"
