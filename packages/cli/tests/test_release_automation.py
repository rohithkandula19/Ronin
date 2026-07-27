"""Regression coverage for the release manifest and artifact gates."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app
from ronin_cli.release import (
    CLI_INTERNAL_DISTRIBUTIONS,
    RELEASE_PACKAGE_DIRS,
    prepare_release,
    release_artifact_errors,
    release_packages,
    release_validation_errors,
    release_version_files,
    validate_release,
    validate_release_artifacts,
    version_from_tag,
)


_NAMES = {
    "packages/agent-patterns": "ronin-agent-patterns",
    "packages/eval-suite": "ronin-eval-suite",
    "packages/memory": "ronin-memory",
    "packages/hardening": "ronin-hardening",
    "packages/mcp-servers": "ronin-mcp-servers",
    "packages/cli": "ronin-cli",
    "packages/relay": "ronin-relay",
}


def _write_project(path: Path, name: str, version: str, dependencies: tuple[str, ...] = ()) -> None:
    dependencies_block = "\n".join(f'  "{dependency}",' for dependency in dependencies)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        f"dependencies = [{dependencies_block}\n]\n",
        encoding="utf-8",
    )


def _release_tree(tmp_path: Path, version: str = "1.0.0") -> Path:
    _write_project(tmp_path / "pyproject.toml", "ronin", version)
    for directory in RELEASE_PACKAGE_DIRS:
        dependencies = tuple(f"{name}=={version}" for name in CLI_INTERNAL_DISTRIBUTIONS)
        _write_project(
            tmp_path / directory / "pyproject.toml",
            _NAMES[directory],
            version,
            dependencies if directory == "packages/cli" else (),
        )
    (tmp_path / "packages/cli/src/ronin_cli").mkdir(parents=True)
    (tmp_path / "packages/cli/src/ronin_cli/__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8",
    )
    (tmp_path / "packages/relay/src/ronin_relay").mkdir(parents=True)
    (tmp_path / "packages/relay/src/ronin_relay/__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize(("tag", "version"), [
    ("v1.2.3", "1.2.3"),
    ("v1.2.3-rc.4", "1.2.3rc4"),
    ("v1.2.3-b.4", "1.2.3b4"),
])
def test_version_from_tag_supports_final_and_prerelease(tag: str, version: str) -> None:
    assert version_from_tag(tag) == version


def test_version_from_tag_refuses_ambiguous_tags() -> None:
    with pytest.raises(ValueError, match="invalid release tag"):
        version_from_tag("release-1.2.3")


def test_release_validation_catches_package_and_dependency_drift(tmp_path: Path) -> None:
    root = _release_tree(tmp_path)
    assert release_validation_errors(root, "v1.0.0") == []

    cli_file = root / "packages/cli/pyproject.toml"
    cli_file.write_text(
        cli_file.read_text(encoding="utf-8").replace("ronin-memory==1.0.0", "ronin-memory==0.9.0"),
        encoding="utf-8",
    )
    (root / "packages/memory/pyproject.toml").write_text(
        (root / "packages/memory/pyproject.toml").read_text(encoding="utf-8").replace(
            'version = "1.0.0"', 'version = "0.9.0"',
        ),
        encoding="utf-8",
    )

    errors = release_validation_errors(root, "v1.0.0")
    assert any("packages/memory/pyproject.toml declares 0.9.0" in error for error in errors)
    assert any("ronin-memory==1.0.0" in error for error in errors)
    with pytest.raises(ValueError, match="release validation failed"):
        validate_release(root, "v1.0.0")


def test_prepare_release_updates_only_the_release_manifest(tmp_path: Path) -> None:
    root = _release_tree(tmp_path)
    unrelated = root / "apps/demo/pyproject.toml"
    _write_project(unrelated, "ronin-demo", "0.0.1")

    changed = prepare_release(root, "1.1.0rc2")

    assert set(changed) == set(release_version_files(root))
    assert release_validation_errors(root, "v1.1.0-rc.2") == []
    assert 'version = "0.0.1"' in unrelated.read_text(encoding="utf-8")


def test_release_cli_synchronizes_the_fixed_manifest(tmp_path: Path) -> None:
    root = _release_tree(tmp_path)

    result = CliRunner().invoke(app, ["release", "patch", "--no-changelog", "--root", str(root)])

    assert result.exit_code == 0, result.stdout
    assert release_validation_errors(root, "v1.0.1") == []


def test_release_artifact_validation_requires_a_wheel_and_sdist_per_package(tmp_path: Path) -> None:
    root = _release_tree(tmp_path)
    dist = root / "dist"
    dist.mkdir()
    for package in release_packages(root):
        prefix = f"{package.name.replace('-', '_')}-1.0.0"
        (dist / f"{prefix}-py3-none-any.whl").touch()
        (dist / f"{prefix}.tar.gz").touch()

    assert release_artifact_errors(root, "v1.0.0", dist) == []
    validate_release_artifacts(root, "v1.0.0", dist)

    (dist / "ronin_cli-1.0.0.tar.gz").unlink()
    errors = release_artifact_errors(root, "v1.0.0", dist)
    assert errors == ["missing sdist for ronin-cli 1.0.0"]


def _fake_command(path: Path, name: str, source: str) -> None:
    command = path / name
    command.write_text(source, encoding="utf-8")
    command.chmod(0o755)


def _release_env(tmp_path: Path, *, git_status: str = "") -> tuple[dict[str, str], Path]:
    commands = tmp_path / "commands"
    commands.mkdir()
    uv_log = tmp_path / "uv.log"
    _fake_command(commands, "git", """#!/usr/bin/env bash
if [ "$1" = "status" ] && [ "$2" = "--porcelain" ]; then
  printf '%s' "${FAKE_GIT_STATUS:-}"
  exit 0
fi
if [ "$1" = "status" ] && [ "$2" = "--short" ]; then
  exit 0
fi
if [ "$1" = "rev-parse" ] && [ "$2" = "--abbrev-ref" ]; then
  printf '%s\\n' main
  exit 0
fi
exit 99
""")
    _fake_command(commands, "uv", """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_UV_LOG"
if [ "$1" = "run" ] && [ "$2" = "--frozen" ] && [ "$3" = "pytest" ]; then
  printf '7 passed\\n'
  exit 0
fi
exit 98
""")
    env = os.environ.copy()
    env.update({
        "PATH": f"{commands}{os.pathsep}{env['PATH']}",
        "FAKE_GIT_STATUS": git_status,
        "FAKE_UV_LOG": str(uv_log),
    })
    return env, uv_log


def test_release_script_dry_run_does_not_change_sources(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    env, _ = _release_env(tmp_path)
    watched = [repo / "CHANGELOG.md", *release_version_files(repo)]
    before = {path: path.read_text(encoding="utf-8") for path in watched}

    result = subprocess.run(
        ["bash", "scripts/release.sh", "1.2.3", "--dry-run"],
        cwd=repo, env=env, text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry run complete" in result.stdout
    assert {path: path.read_text(encoding="utf-8") for path in watched} == before


def test_release_script_refuses_a_dirty_tree_before_tests(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    env, uv_log = _release_env(tmp_path, git_status=" M packages/cli/src/ronin_cli/main.py\n")

    result = subprocess.run(
        ["bash", "scripts/release.sh", "1.2.3", "--dry-run"],
        cwd=repo, env=env, text=True, capture_output=True,
    )

    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    assert not uv_log.exists()
