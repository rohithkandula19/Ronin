"""Tool registry / gate drift guards (Stage A blocker 0.3).

The approval gate keys off ``SENSITIVE_TOOLS`` (plus any tool flagged
``.sensitive``). Two ways that can drift:

  * DANGEROUS: a mutating tool reaches the toolbelt but is NOT gated -> it would
    bypass approval. ``ungated_mutators`` catches this and code_mode refuses.
  * HARMLESS-BUT-STALE: a name in ``SENSITIVE_TOOLS`` that no real tool backs.
    ``phantom_sensitive`` catches this; run_background/rewind are built in
    sibling modules (EXTERNAL_SENSITIVE_TOOLS), so they are NOT phantom.
"""
from __future__ import annotations

from ronin_cli.bg_processes import build_background_tools
from ronin_cli.checkpoint import build_checkpoint_tools
from ronin_cli.code_tools import (
    EXTERNAL_SENSITIVE_TOOLS,
    SENSITIVE_TOOLS,
    build_code_tools,
    phantom_sensitive,
    ungated_mutators,
)


def _all_tool_names(root) -> set[str]:
    tools = (
        build_code_tools(root)
        + build_checkpoint_tools(root)
        + build_background_tools(root)
    )
    return {t.name for t in tools}


def test_every_sensitive_tool_really_exists(tmp_path) -> None:
    # No phantom: every gated name is backed by a real built tool somewhere.
    names = _all_tool_names(tmp_path)
    assert SENSITIVE_TOOLS <= names, f"gated but not built: {SENSITIVE_TOOLS - names}"
    assert phantom_sensitive(names) == set()


def test_external_sensitive_tools_are_built_elsewhere(tmp_path) -> None:
    # The two tools not built by build_code_tools ARE built by their modules.
    builtin_names = {t.name for t in build_code_tools(tmp_path)}
    assert EXTERNAL_SENSITIVE_TOOLS.isdisjoint(builtin_names)
    checkpoint = {t.name for t in build_checkpoint_tools(tmp_path)}
    bg = {t.name for t in build_background_tools(tmp_path)}
    assert "rewind" in checkpoint
    assert "run_background" in bg


def test_no_mutating_tool_is_ungated(tmp_path) -> None:
    # The dangerous direction: with the real gate set, nothing mutating slips past.
    names = _all_tool_names(tmp_path)
    assert ungated_mutators(names, SENSITIVE_TOOLS) == set()


def test_ungated_mutator_is_detected() -> None:
    # The guard actually fires when a mutating tool is present but not gated.
    assert ungated_mutators({"write_file", "read_file"}, {"read_file"}) == {"write_file"}
    assert ungated_mutators({"read_file", "list_files"}, {"read_file"}) == set()


def test_builtin_mutators_present_and_gated(tmp_path) -> None:
    builtin = {t.name for t in build_code_tools(tmp_path)}
    for mutator in ("write_file", "edit_file", "multi_edit", "run_command"):
        assert mutator in builtin
        assert mutator in SENSITIVE_TOOLS
