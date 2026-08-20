"""``ronin doctor`` with an offline ``Detection``: config, model access, and ripgrep.

The detection checks fire only when a ``Detection`` is passed, so every existing call
site that passes none is untouched (asserted below). When one is passed, each finding
carries the exact fix a first-run user acts on — the two example remedies from the spec
appear verbatim when things are missing, and a fully-provisioned machine comes out clean.
"""

from __future__ import annotations

from pathlib import Path

from wire_harness import fake_checkpoint_store, fake_router, workspace, write

from ronin.cli.detect import Detection
from ronin.cli.doctor import (
    NO_CONFIG_REMEDY,
    NO_MODEL_REMEDY,
    RIPGREP_REMEDY,
    CheckStatus,
    run_doctor,
)
from ronin.cli.wire import load_workspace

BARE = Detection(keys=(), local_servers=(), ripgrep=False)
PROVISIONED = Detection(keys=("anthropic",), local_servers=("ollama",), ripgrep=True)


async def test_no_keys_and_no_ripgrep_report_the_exact_fix_strings(tmp_path: Path) -> None:
    loaded = load_workspace(workspace(tmp_path))

    report = await run_doctor(
        loaded,
        router=fake_router(),
        environ={},
        checkpoints=fake_checkpoint_store(tmp_path),
        detection=BARE,
    )
    body = report.render()

    providers = report.check("providers")
    assert providers is not None
    assert providers.status is CheckStatus.FAIL
    assert providers.remedy == NO_MODEL_REMEDY
    assert "no model configured — set ANTHROPIC_API_KEY" in body
    assert "copy examples/models.toml" in body

    ripgrep = report.check("ripgrep")
    assert ripgrep is not None
    assert ripgrep.status is CheckStatus.WARN
    assert ripgrep.remedy == RIPGREP_REMEDY
    assert "no ripgrep found — install with `brew install ripgrep` / `apt install ripgrep`" in body

    config = report.check("config")
    assert config is not None
    assert config.status is CheckStatus.WARN
    assert config.remedy == NO_CONFIG_REMEDY

    # No model at all is the one detection finding that fails the run.
    assert report.exit_code() == 1


async def test_a_provisioned_machine_is_clean(tmp_path: Path) -> None:
    paths = workspace(tmp_path)
    # A models.toml on disk is what makes the config check resolve.
    write(tmp_path, ".ronin/models.toml", "# present\n")
    loaded = load_workspace(paths)

    report = await run_doctor(
        loaded,
        router=fake_router(),
        environ={},
        checkpoints=fake_checkpoint_store(tmp_path),
        detection=PROVISIONED,
    )

    providers = report.check("providers")
    assert providers is not None
    assert providers.status is CheckStatus.OK
    assert "anthropic" in providers.detail
    assert "ollama" in providers.detail

    ripgrep = report.check("ripgrep")
    assert ripgrep is not None
    assert ripgrep.status is CheckStatus.OK

    config = report.check("config")
    assert config is not None
    assert config.status is CheckStatus.OK

    # None of the detection checks is a failure. (The role-map check may still warn that
    # `fast`/`plan` are unmapped in the fake router — that is not what this asserts.)
    failed = {check.name for check in report.failures}
    assert {"providers", "ripgrep", "config"}.isdisjoint(failed)


async def test_without_a_detection_no_detection_checks_appear(tmp_path: Path) -> None:
    loaded = load_workspace(workspace(tmp_path))

    report = await run_doctor(
        loaded,
        router=fake_router(),
        environ={},
        checkpoints=fake_checkpoint_store(tmp_path),
    )

    assert report.check("providers") is None
    assert report.check("ripgrep") is None
    assert report.check("config") is None
