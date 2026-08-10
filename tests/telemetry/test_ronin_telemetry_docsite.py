"""The docs-site staleness gate, plus assertions that the examples are runnable.

Hand-written reference documentation drifts the day it is written, so the reference pages
in ``docs/site/`` are generated from the code they document and this module fails when a
page on disk disagrees with what the generator now renders. Same contract as
``scripts/sync_typecheck_paths.py --check`` and
``scripts/generate_readme_stats.py --check``: the fix is to run the generator, and the
failure names the file.

The second half is about ``examples/workflows/``. A copyable example containing a flag
that does not exist is worse than no example, so every long flag those files use is
checked against ``ronin.cli.main.build_parser`` and every config they ship is fed to the
real loader.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from telemetry_harness import REPO_ROOT, load_docsite_generator, rendered_pages

from ronin.cli.main import build_parser
from ronin.providers.router import parse_config
from ronin.safety.settings import SCALAR_KEYS
from ronin.tools.registry import ToolRegistry

SITE = REPO_ROOT / "docs" / "site"
WORKFLOWS = REPO_ROOT / "examples" / "workflows"


# --------------------------------------------------------------------------- #
# staleness
# --------------------------------------------------------------------------- #


def test_every_generated_page_matches_the_live_code() -> None:
    module = load_docsite_generator()
    drifted = module.stale()
    assert drifted == [], f"docs/site pages are stale: {drifted}. Run: python docs/site/generate.py"


@pytest.mark.parametrize(
    "name", ["tools.md", "config.md", "hooks.md", "subagents.md", "providers.md", "telemetry.md"]
)
def test_each_generated_page_exists_and_is_marked_generated(name: str) -> None:
    module = load_docsite_generator()
    path = SITE / name
    assert path.is_file(), f"{path} is missing; run python docs/site/generate.py"
    # Bytes, because read_text applies universal-newline translation and would hide a
    # CRLF regression in a file the generator writes with newline="\n".
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.decode("utf-8").startswith(module.MARKER)


def test_the_generated_page_list_agrees_with_what_render_all_produces() -> None:
    module = load_docsite_generator()
    assert tuple(sorted(rendered_pages())) == tuple(sorted(module.GENERATED_PAGES))


def test_the_hand_written_pages_exist_and_are_not_marked_generated() -> None:
    module = load_docsite_generator()
    for name in ("index.md", "quickstart.md"):
        path = SITE / name
        assert path.is_file(), path
        assert not path.read_text(encoding="utf-8").startswith(module.MARKER)


def test_no_markdown_file_in_the_site_is_unaccounted_for() -> None:
    """A page nobody generates and nobody hand-maintains is a page that rots."""
    module = load_docsite_generator()
    known = set(module.GENERATED_PAGES) | {"index.md", "quickstart.md"}
    found = {path.name for path in SITE.glob("*.md")}
    assert found == known


# --------------------------------------------------------------------------- #
# the tool reference against the real registry
# --------------------------------------------------------------------------- #


def test_the_tool_reference_names_every_tool_the_registry_has(tmp_path: Path) -> None:
    module = load_docsite_generator()
    registry: ToolRegistry = module.full_registry(tmp_path)
    page = (SITE / "tools.md").read_text(encoding="utf-8")
    for spec in registry.specs():
        assert f"## {spec.name}\n" in page, spec.name
        # The description a model receives, verbatim. If this drifts, the page is
        # documenting a prompt the model does not get.
        assert spec.description.splitlines()[0] in page


def test_the_tool_reference_carries_each_tool_s_real_danger_level(tmp_path: Path) -> None:
    module = load_docsite_generator()
    registry: ToolRegistry = module.full_registry(tmp_path)
    page = (SITE / "tools.md").read_text(encoding="utf-8")
    for spec in registry.specs():
        heading = page.split(f"## {spec.name}\n", 1)[1]
        first_line = heading.strip().splitlines()[0]
        assert spec.danger_level.name.lower() in first_line
        assert str(spec.requires_approval).lower() in first_line


def test_the_tool_reference_says_which_tools_a_plain_session_lacks(tmp_path: Path) -> None:
    """The claim people trip over. Derived from the same call ``wire`` makes, so it
    cannot be true today and wrong after someone widens the wiring."""
    module = load_docsite_generator()
    full = {spec.name for spec in module.full_registry(tmp_path).specs()}
    plain = {spec.name for spec in module.cli_registry(tmp_path).specs()}
    plain |= set(module.SESSION_ADDED_TOOLS)
    page = (SITE / "tools.md").read_text(encoding="utf-8")
    for name in sorted(full - plain):
        assert f"| [`{name}`]" in page
        row = next(line for line in page.splitlines() if line.startswith(f"| [`{name}`]"))
        assert "needs injection" in row


# --------------------------------------------------------------------------- #
# the config reference against the real loader
# --------------------------------------------------------------------------- #


def test_the_config_reference_documents_every_settings_key() -> None:
    page = (SITE / "config.md").read_text(encoding="utf-8")
    for key in SCALAR_KEYS:
        assert f"`{key}`" in page, key


def test_the_config_reference_lists_the_layers_in_loader_order() -> None:
    """The order is the whole point: a documented precedence that disagrees with the
    loader is worse than no documentation, because it is believed."""
    from ronin.safety.settings import LAYER_NAMES

    page = (SITE / "config.md").read_text(encoding="utf-8")
    positions = [page.index(f"| {index + 1} | `{name}`") for index, name in enumerate(LAYER_NAMES)]
    assert positions == sorted(positions)


# --------------------------------------------------------------------------- #
# the examples: every flag exists
# --------------------------------------------------------------------------- #


#: Every long option the parser accepts, taken from the parser rather than a list.
def _known_long_options() -> set[str]:
    found: set[str] = set()
    for action in build_parser()._actions:
        found.update(option for option in action.option_strings if option.startswith("--"))
    return found


#: Long options that appear in the examples' shell/yaml but are *not* Ronin's — they
#: belong to git, gh, pip, jq or actions. Enumerated so a genuine typo in a Ronin flag
#: still fails, instead of the check being switched off.
FOREIGN_LONG_OPTIONS: frozenset[str] = frozenset(
    {
        "--cached",
        "--quiet",
        "--exit-code",
        "--unified=3",
        "--no-color",
        "--show-toplevel",
        "--porcelain",
        "--log-failed",
        "--draft",
        "--title",
        "--body",
        "--no-verify",
        "--abbrev-ref",
        "--recursive",
        "--write",
    }
)


@pytest.mark.parametrize(
    "relative",
    [
        "pre-commit-reviewer/pre-commit",
        "repo-onboarding-explainer/onboard.sh",
        "ci-failure-fixer/ronin-fix.yml",
    ],
)
def test_every_ronin_flag_used_by_an_example_exists(relative: str) -> None:
    text = (WORKFLOWS / relative).read_text(encoding="utf-8")
    known = _known_long_options()
    used = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*(?:=[^\s\"']+)?", text))
    unknown = {flag for flag in used if flag not in known and flag not in FOREIGN_LONG_OPTIONS}
    assert unknown == set(), f"{relative} uses flags the parser does not have: {sorted(unknown)}"


@pytest.mark.parametrize(
    "relative",
    [
        "pre-commit-reviewer/pre-commit",
        "repo-onboarding-explainer/onboard.sh",
        "ci-failure-fixer/ronin-fix.yml",
    ],
)
def test_no_example_invokes_the_v1_console_script(relative: str) -> None:
    """``ronin`` is ``packages/cli``'s entry point, not this application's. An example
    that types ``ronin -p`` runs a different program."""
    text = (WORKFLOWS / relative).read_text(encoding="utf-8")
    # `-m ronin` is the module form and is correct; a bare `ronin` followed by one of
    # this application's verbs is the v1 console script and is not.
    bad = re.findall(r"(?<!-m )(?<![-\w./])ronin\s+(?:-p|doctor|sessions|export)\b", text)
    assert bad == [], bad


@pytest.mark.parametrize(
    "relative",
    [
        "pre-commit-reviewer/pre-commit",
        "repo-onboarding-explainer/onboard.sh",
        "ci-failure-fixer/ronin-fix.yml",
        "ci-failure-fixer/README.md",
        "pre-commit-reviewer/README.md",
        "repo-onboarding-explainer/README.md",
        "README.md",
    ],
)
def test_no_example_reaches_for_yolo(relative: str) -> None:
    """``--yolo`` waives the deny list itself. An example is a thing people copy without
    reading, so none of them may contain it outside a warning."""
    text = (WORKFLOWS / relative).read_text(encoding="utf-8")
    for line in text.splitlines():
        if "--yolo" not in line:
            continue
        lowered = line.lower()
        assert any(word in lowered for word in ("not", "never", "absent", "waive", "warning")), (
            f"{relative}: --yolo appears without a warning: {line}"
        )


@pytest.mark.parametrize("mode", ["plan", "auto_edit"])
def test_the_modes_the_examples_pass_are_real_modes(mode: str) -> None:
    from ronin.core.types import Mode

    assert Mode(mode)


# --------------------------------------------------------------------------- #
# the examples: every config parses
# --------------------------------------------------------------------------- #


def test_every_example_settings_file_loads_with_no_errors() -> None:
    """An unknown key is an error in the real loader, and a shipped example with one
    would put a red line in front of everyone who copied it."""
    from ronin.safety.settings import _mapping_layer

    found = sorted(WORKFLOWS.rglob("settings.json"))
    assert found, "no example settings.json to check"
    for path in found:
        data = json.loads(path.read_text(encoding="utf-8"))
        layer = _mapping_layer("project", path, data)
        assert layer.errors == (), (path, [str(error) for error in layer.errors])
        assert layer.rules, f"{path} declares no rules"


def test_every_example_models_toml_parses() -> None:
    found = sorted(WORKFLOWS.rglob("models.toml"))
    assert found, "no example models.toml to check"
    for path in found:
        config = parse_config(tomllib.loads(path.read_text(encoding="utf-8")))
        assert config.roles, path


def test_the_shell_examples_are_executable_and_have_a_shebang() -> None:
    for relative in ("pre-commit-reviewer/pre-commit", "repo-onboarding-explainer/onboard.sh"):
        path = WORKFLOWS / relative
        raw = path.read_bytes()
        assert raw.startswith(b"#!/bin/sh"), relative
        assert b"\r\n" not in raw, f"{relative} has CRLF line endings and will not run"
        assert path.stat().st_mode & 0o111, f"{relative} is not executable"


def test_every_workflow_directory_has_a_readme() -> None:
    directories = sorted(path for path in WORKFLOWS.iterdir() if path.is_dir())
    assert len(directories) == 3
    for directory in directories:
        assert (directory / "README.md").is_file(), directory


def test_every_workflow_readme_states_whether_it_needs_a_key_or_network() -> None:
    """The question a reader has before they copy anything."""
    for directory in sorted(path for path in WORKFLOWS.iterdir() if path.is_dir()):
        text = (directory / "README.md").read_text(encoding="utf-8").lower()
        assert "api key" in text or "needs a model" in text, directory
        assert "what it costs" in text, directory
        assert "what could go wrong" in text or "could go wrong" in text, directory
